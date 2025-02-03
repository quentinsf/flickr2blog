#! /usr/bin/env python3
# 
# Flickr API docs are here:
# https://www.flickr.com/services/api/
#
# Python wrapper for WP XML-RPC API docs are here:
# https://python-wordpress-xmlrpc.readthedocs.io/

import argparse
from datetime import datetime
import json
import os
import re
import requests

import flickrapi

from dynaconf import Dynaconf

settings = Dynaconf(
    envvar_prefix="F2B",
    settings_files=['config.toml', '.secrets.toml'],
    load_dotenv=True,
)

def wp_rest_url(method):
    return f"{settings.wordpress_url}/wp-json/wp/v2/{method}"

def get_wp():
    wp = requests.Session()
    wp.auth = (settings.wordpress_username, settings.wordpress_password)
    return wp

def get_flickr():
    return flickrapi.FlickrAPI(settings.flickr_api_key, settings.flickr_api_secret, format='parsed-json')

# How will we recognise a flickr URL?
flickr_photo_re = re.compile(r'(https?://(?:www\.)?flickr\.com/photos/quentinsf/(\d{9,11})/?)[/"]')

def post_retriever(wp, offset=0):
    increment = 50
    while True:
        resp = wp.get(wp_rest_url("posts"), params = {
            "offset": offset,
            "per_page": increment
        })
        posts = resp.json()
        if len(posts) == 0:
            break  # no more posts returned
        for post in posts:
            yield post
        offset = offset + increment

def read_post_catalog(filename):
    with open(filename, "r") as post_catalog: 
        print("Reading posts from", filename)
        posts = json.load(post_catalog)
    return posts

def write_post_catalog(filename, posts):
    with open(filename, "w") as output:
        print(f"Writing {len(posts)} posts to {filename}")
        json.dump(posts, output, indent=2)

def catalog_posts(args):
    arg_dict = vars(args)
    wp = get_wp()
    count = 0
    limit = arg_dict.get('limit', 0)
    posts = []
    for post in post_retriever(wp, arg_dict.get('offset', 0)):
        content =  post['content']['rendered']
        if "flickr.com" in content:
            print(post['id'], post['title']['rendered'], post['link'])
            flickr_images = []
            for match in flickr_photo_re.finditer(content):
                # Let's store the key info in the posts catalog:
                img_info = {
                    "flickr_id": match.group(2),
                    "url": match.group(1),
                    "url_start": match.start(1),
                    "url_end": match.end(1)
                }
                print(f"   {img_info['flickr_id']} at {img_info['url']}")
                flickr_images.append(img_info)
            post['flickr_images'] = flickr_images
            posts.append(post)
            count += 1
            if limit and count >= limit:
                break
    write_post_catalog(args.output, posts)


def read_image_catalog(filename):
    with open(filename, "r") as image_catalog: 
        print("Reading images from", filename)
        images = json.load(image_catalog)
    return images

def write_image_catalog(filename, images):
    with open(filename, "w") as output:
        print(f"Writing {len(images)} images to {filename}")
        json.dump(images, output, indent=2)

def catalog_images(args):
    posts = read_post_catalog(args.post_catalog)
    print(len(posts), "posts to process")

    flickr = get_flickr()
    photos = []
    for post in posts:
        print(post['id'], post['title']['rendered'], post['link'])
        for image_info in post['flickr_images']:
            flickr_id = image_info['flickr_id']
            print("  ", flickr_id)
            flickr_info = flickr.photos.getInfo(photo_id=flickr_id)            # print("  F:", flickr_info)

            size_info = flickr.photos.getSizes(photo_id=flickr_id)['sizes']['size']
            sizes = { s['label']: s  for s in size_info}
            flickr_info['sizes'] = sizes
            photos.append(flickr_info)
    
    write_image_catalog(args.output, photos)



def download_images(args):
    """
    Grab a medium and an original size.
    Don't download if already existing.
    """

    photos = read_image_catalog(args.image_catalog)
    print(len(photos), "images to process")

    flickr = get_flickr()
    
    def download_size(url, filename):
        os.makedirs(settings.download_dir, exist_ok=True)
        path = os.path.join(settings.download_dir, filename)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(requests.get(url).content)
        else:
            print("    ", path, "exists")

    def download_photo(flickr_id, sizes):
        smaller_file = None
        if 'Medium 800' in sizes:
            smaller_file = f"{flickr_id}_800.jpg"
            download_size(sizes['Medium 800']['source'], smaller_file)
        elif 'Medium 640' in sizes:
            smaller_file = f"{flickr_id}_640.jpg"
            download_size(sizes['Medium 640']['source'], smaller_file)
        else:
            print("    No Medium 800 - options:", sizes)
        original_file = f"{flickr_id}.jpg"
        download_size(sizes['Original']['source'], original_file)
        return (original_file, smaller_file)

    for photo in photos:
        flickr_id = photo['photo']['id']
        sizes = photo['sizes']
        (original_file, smaller_file) = download_photo(flickr_id, sizes)

    

def upload_to_wp(args):
    """
    Upload and associate images with the posts, but don't chenge the text yet.
    Warning:  this overwrites the post catalog, so if you use 'limit', you will
    truncate it unless you specify a new_post_catalog setting.
    """
    posts = read_post_catalog(args.post_catalog)
    print(len(posts), "posts in catalog")
    if hasattr(args, 'limit'):
        print(f"Limiting action to {args.limit} posts")
        posts = posts[:args.limit]

    # images = read_image_catalog(args.image_catalog)
    # image_map = {p['photo']['id']: p for p in images}

    wp = get_wp()

    def upload_media(photo_file, dest_path, data):
        local_path = os.path.join(settings.download_dir, photo_file)
        print("Uploading", local_path, "to", dest_path)
        with open(local_path, 'rb') as file:
            response = wp.post(
                wp_rest_url('media'),
                data = data,
                files={ 'file': (photo_file, file) }
            )
            assert response.status_code == 201
        response_data = response.json()
        attachment_url = response_data['source_url']
        print("   to", attachment_url)
        return attachment_url

    for post in posts:
        post['upload_info'] = {}
        print(f"Post {post['id']} at {post['link']} has these flickr ids:")
        for img_info in post['flickr_images']:
            flickr_id = img_info['flickr_id']
            print("  ", flickr_id)
            photo_data = {
                "post": post['id'],
                "date": post['date'],
                "date_gmt": post['date_gmt'],
                "description": f"Flickr item {flickr_id}."
            }
            original_url = upload_media(f"{flickr_id}.jpg", f"{flickr_id}.jpg", photo_data)
            medium_url = upload_media(f"{flickr_id}_800.jpg", f"{flickr_id}_800.jpg", photo_data)
            post['upload_info'][flickr_id] = {
                "original_url": original_url,
                "medium_url": medium_url
            }
    write_post_catalog(args.new_post_catalog, posts)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(description="Run subcomands with '-h' for syntax")

    parser_catalog_posts = subparsers.add_parser('catalog_posts', help="Get the details of posts to be processed")
    parser_catalog_posts.add_argument('--offset', type=int, default=0)
    parser_catalog_posts.add_argument('--limit', type=int)
    parser_catalog_posts.add_argument('--output', type=str, default="posts.json", help="Output catalog file, default '%(default)s'")
    parser_catalog_posts.set_defaults(func=catalog_posts)

    parser_catalog_images = subparsers.add_parser('catalog_images', help="Get the details of images to be downloaded for the posts")
    parser_catalog_images.add_argument('--post_catalog', type=str, default="posts.json", help="Post catalog file to read, default '%(default)s'")
    parser_catalog_images.add_argument('--output', type=str, default="images.json", help="Output image catalog file, default '%(default)s'")
    parser_catalog_images.set_defaults(func=catalog_images)

    parser_download_images = subparsers.add_parser('download_images', help="Get images referred to in image catalog.")
    parser_download_images.add_argument('--image_catalog', type=str, default="images.json")
    parser_download_images.set_defaults(func=download_images)

    parser_upload_to_wp = subparsers.add_parser('upload_to_wp', help="Upload images and attach to posts.")
    parser_upload_to_wp.add_argument('--post_catalog', type=str, default="posts.json", help="Post catalog file to read, default '%(default)s'")
    parser_upload_to_wp.add_argument('--new_post_catalog', type=str, default="posts.json", help="Post catalog file to overwrite, default also '%(default)s'")
    parser_upload_to_wp.add_argument('--image_catalog', type=str, default="images.json", help="Image catalog file to read, default '%(default)s'")
    parser_upload_to_wp.add_argument('--limit', type=int, help="Stop after this many posts. You may also want to set new_post_catalog.")
    parser_upload_to_wp.set_defaults(func=upload_to_wp)

    args = parser.parse_args()
    args.func(args)
    


    

if __name__ == "__main__":
    main()
