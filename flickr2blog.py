#! /usr/bin/env python3
# 
# Flickr API docs are here:
# https://www.flickr.com/services/api/

import os
import re
import requests

import flickrapi

from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media
from wordpress_xmlrpc.methods.posts import GetPosts, EditPost

from dynaconf import Dynaconf

settings = Dynaconf(
    envvar_prefix="F2B",
    settings_files=['config.toml', '.secrets.toml'],
    load_dotenv=True,
)


def post_retriever(wp, offset=0):
    increment = 50
    while True:
        posts = wp.call(GetPosts({'number': increment, 'offset': offset}))
        if len(posts) == 0:
            break  # no more posts returned
        for post in posts:
            yield post
        offset = offset + increment

def main():
    
    # Initialize WordPress client
    wp = Client(settings.wordpress_url, settings.wordpress_username, settings.wordpress_password)

    # Initialize Flickr API
    flickr = flickrapi.FlickrAPI(settings.flickr_api_key, settings.flickr_api_secret, format='parsed-json')

    def download_size(url, filename):
        os.makedirs(settings.download_dir, exist_ok=True)
        path = os.path.join(settings.download_dir, filename)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(requests.get(url).content)

    def download_photo(flickr_id, sizes):
        if 'Medium 800' in sizes:
            download_size(sizes['Medium 800'][0], f"{flickr_id}_800.jpg")
        elif 'Medium 640' in sizes:
            download_size(sizes['Medium 640'][0], f"{flickr_id}_640.jpg")
        else:
            print("    No Medium 800 - options:", sizes)
        download_size(sizes['Original'][0], f"{flickr_id}.jpg")
        
    
    def get_sizes(photo_id: str):
        size_info = flickr.photos.getSizes(photo_id=flickr_id)
        return { s['label']: (s['source'], s['width'], s['height']) 
                  for s in size_info['sizes']['size']}


    # flickr_re = re.compile(r'https?://(?:www\.)?flickr\.com/photos/[^\s<>"]+')
    flickr_photo_re = re.compile(r'(https?://(?:www\.)?flickr\.com/photos/quentinsf/(\d{9,10})/)')
    # Process each post and replace Flickr photos
    for post in post_retriever(wp, 0):

        if "flickr.com" in post.content:
            print(post.id, post.title, post.link)
            flickr_urls = flickr_photo_re.findall(post.content)
            for flickr_url, flickr_id in flickr_urls:
                print("  ", flickr_url, flickr_id)
                # print("  F:", flickr.photos.getInfo(photo_id=flickr_id))
                sizes = get_sizes(flickr_id)
                download_photo(flickr_id, sizes)
                # photo_data = download_photo(flickr_url)
                # photo_title = flickr_url.split('/')[-1]
                # new_photo_url = upload_to_wordpress(photo_data, photo_title)
                # post_content = post_content.replace(flickr_url, new_photo_url)
        

if __name__ == "__main__":
    main()
