#
#!/usr/bin/env python
# Copyright 2007 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import os
import urllib
import jinja2
import webapp2
import re
import time

from google.appengine.api import users
from google.appengine.api import images
from google.appengine.ext import ndb
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers

from feedformatter import Feed


JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)


DEFAULT_GUESTBOOK_NAME = 'default_guestbook'
DEFAULT_USER_NAME = 'guest'


# We set a parent key on the 'Greetings' to ensure that they are all in the same
# entity group. Queries across the single entity group will be consistent.
# However, the write rate should be limited to ~1/second.

def guestbook_key(guestbook_name=DEFAULT_GUESTBOOK_NAME):
    """Constructs a Datastore key for a Guestbook entity with guestbook_name."""
    return ndb.Key('Guestbook', guestbook_name)

def user_key(user_name):
    return ndb.Key('User', user_name)

def blog_key(blog_name):
    return ndb.Key('Blog', blog_name)

class User(ndb.Model):
    name = ndb.UserProperty()
    follows = ndb.StringProperty(repeated=True)

class Blog(ndb.Model):
    owner = ndb.UserProperty()
    authors = ndb.StringProperty(repeated=True)
    name = ndb.StringProperty()

class Post(ndb.Model):
    blog_key = ndb.KeyProperty(kind=Blog, required=True)
    blog_name = ndb.StringProperty()
    title = ndb.StringProperty()
    author = ndb.UserProperty()
    content = ndb.StringProperty(indexed=False)
    imgs_url = ndb.StringProperty(repeated=True)
    create_time = ndb.DateTimeProperty(auto_now_add=True)
    edit_time = ndb.DateTimeProperty(auto_now=True)
    tags = ndb.StringProperty(repeated=True)
    views = ndb.IntegerProperty()

class Greeting(ndb.Model):
    """Models an individual Guestbook entry with author, content, and date."""
    author = ndb.UserProperty()
    content = ndb.StringProperty(indexed=False)
    date = ndb.DateTimeProperty(auto_now_add=True)

def mergeListsWithOutDuplicates(result, lst):
    for e in lst:
        if e not in result:
            result.append(e)

class MainPage(webapp2.RequestHandler):

    def get(self):
        current_user = users.get_current_user()
        blog_name = self.request.get('blog_name')
        current_tag = self.request.get('tag')
        create_blog = self.request.get('create_blog')
        posts = []
        is_editor = False
        if current_user:
                is_editor = Blog.query(Blog.name == blog_name, Blog.authors.IN([current_user.nickname()])).get()
        if blog_name:
            blog_posts_query = Post.query(Post.blog_key == blog_key(blog_name)).order(-Post.edit_time)
            posts = blog_posts_query.fetch()
        if current_tag:
            posts = Post.query(Post.tags == current_tag).order(-Post.edit_time).fetch()

        newer_pages = False
        older_pages = False
        page_value = self.request.get('page')
        if page_value is "":
            page = 0
        else:
            page = int(page_value)

        if page < 0 or page > (len(posts) % 10) + 1:
            self.redirect('/?blog_name=' + blog_name)
              
        if page > 0:
            newer_pages = True 
        if len(posts) > 10 * (page + 1):
            older_pages = True
        posts = posts[page * 10 : page * 10 + 10]

        if users.get_current_user():
            current_user = users.get_current_user()
            url = users.create_logout_url(self.request.uri)
            url_linktext = 'Logout'
            is_guest = None
        else:
            current_user = 'Guest'
            url = users.create_login_url(self.request.uri)
            url_linktext = 'Login'
            is_guest = 'true'

        template_values = {
            'current_user': current_user,
            'is_guest': is_guest,
            'blog_name': blog_name,
            'is_editor': is_editor, 
            'blogs': Blog.query(),
            'posts': posts,
            'url': url,
            'url_linktext': url_linktext,
            'create_blog': create_blog,
            'older_pages': older_pages,
            'newer_pages': newer_pages,
            'page': page,
        }

        header = JINJA_ENVIRONMENT.get_template('header.html')
        self.response.write(header.render(template_values))
       
        tags = []
        ps = Post.query(Post.tags != "").fetch()
        for p in ps:
            mergeListsWithOutDuplicates(tags, p.tags)

        showtags = JINJA_ENVIRONMENT.get_template('showtags.html')
        self.response.write(showtags.render({
            'tags': tags,  
        }))
        
        view = JINJA_ENVIRONMENT.get_template('view.html')
        self.response.write(view.render(template_values))
        
        if is_editor:
            upload_url = blobstore.create_upload_url('/upload')
            newpost = JINJA_ENVIRONMENT.get_template('newpost.html')
            self.response.write(newpost.render({
                'current_user': current_user,
                'blog_name': blog_name,
                'upload_url': upload_url,
            }))


class Createblog(webapp2.RequestHandler):
    def post(self):
        user_name = self.request.get('user_name')
        blog_name = self.request.get('newblog_name')  

        if not blog_name == "" and Blog.query(Blog.name == blog_name).get() is None:
            authors = []
            authors.append(user_name)

            blog = Blog(parent=user_key(user_name))
            blog.owner = users.User(user_name)
            blog.authors = authors
            blog.name = blog_name
            blog.put();
        
        query_params = {'guestbook_name': user_name}
        self.redirect('/?' + urllib.urlencode(query_params))

def getImageUrl(upload_files):
    if len(upload_files) == 0:
        return None
    else:
        return images.get_serving_url(upload_files[0].key())

class Newpost(blobstore_handlers.BlobstoreUploadHandler):
    
    def post(self):
        if self.request.get('title'):
            post_id = self.request.get('post_id')
            if post_id:
                post = ndb.Key(Post, int(post_id)).get()
            else:
                post = Post(blog_key=blog_key(self.request.get('blog_name')))
            post.title = self.request.get('title')
            post.blog_name = self.request.get('blog_name')
            post.author = users.User(self.request.get('current_user'))
            
            raw_content = self.request.get('content')
            check_content = re.sub(r'(https?://[\w\.-/]+(png|PNG|jpg|JPG|gif|GIF))', r'<img src=\1>', raw_content)
            if raw_content == check_content:
                check_content = re.sub(r'(https?://[\w\.-/]+)', r'<a href=\1>\1</a>', raw_content)
            post.content = check_content

            imgs = []
            
            img_url = getImageUrl(self.get_uploads('img1'))
            if img_url is not None:
                imgs.append(img_url)
            
            img_url = getImageUrl(self.get_uploads('img2'))
            if img_url is not None:
                imgs.append(img_url)

            img_url = getImageUrl(self.get_uploads('img3'))
            if img_url is not None:
                imgs.append(img_url)

            post.imgs_url.extend(imgs)
            post.tags = self.request.get('tags').split(",")
            post.views = 0
            post.put()
        self.redirect('/?blog_name=' + self.request.get('blog_name'))

class Viewpost(webapp2.RequestHandler):

    def get(self):
        current_user = users.get_current_user()
        blog_name = self.request.get('blog_name')
        create_blog = self.request.get('create_blog')
        posts = []
        is_editor = False
        if current_user:
            is_editor = Blog.query(Blog.name == blog_name, Blog.authors.IN([current_user.nickname()])).get()
        if blog_name:
            #blog_posts_query = Post.query(Post.blog_key == blog_key(blog_name)).order(-Post.edit_time)
            post = ndb.Key(Post, int(self.request.get('post_id'))).get()
            if post.views is not None:
                post.views += 1
            post.put()
            #post = Post.query(Post.key.id == int(self.request.get('post_id')))

        if users.get_current_user():
            current_user = users.get_current_user()
            url = users.create_logout_url(self.request.uri)
            url_linktext = 'Logout'
            is_guest = None
        else:
            current_user = 'Guest'
            url = users.create_login_url(self.request.uri)
            url_linktext = 'Login'
            is_guest = 'true'

        template_values = {
            'current_user': current_user,
            'is_guest': is_guest,
            'blog_name': blog_name,
            'is_editor': is_editor, 
            'blogs': Blog.query(),
            'post_id': self.request.get('post_id'),
            'post': post,
            'url': url,
            'url_linktext': url_linktext,
            'create_blog': create_blog,
        }

        header = JINJA_ENVIRONMENT.get_template('header.html')
        self.response.write(header.render(template_values))
        
        tags = []
        ps = Post.query(Post.tags != "").fetch()
        for p in ps:
            mergeListsWithOutDuplicates(tags, p.tags)

        showtags = JINJA_ENVIRONMENT.get_template('showtags.html')
        self.response.write(showtags.render({
            'tags': tags,  
        }))


        content = JINJA_ENVIRONMENT.get_template('viewcontent.html')
        self.response.write(content.render(template_values))

        if self.request.get('edit_mode') == 'true':
            upload_url = blobstore.create_upload_url('/upload')
            newpost = JINJA_ENVIRONMENT.get_template('newpost.html')
            self.response.write(newpost.render({
                'current_user': current_user,
                'blog_name': blog_name,
                'upload_url': upload_url,
                'post_id': self.request.get('post_id'),
                'post_title': post.title,
                'post_content': post.content,
                'post_tags': ','.join(post.tags),
            }))

class RSSGen(webapp2.RequestHandler):
    
    def get(self):
        feed = Feed()
        blog_name = self.request.get('blog_name')
        blog = Blog.query(Blog.name == blog_name).fetch()
        posts = Post.query(Post.blog_key == blog_key(blog_name)).order(-Post.edit_time).fetch()

        feed.feed['title'] = "Rss blog feed"
        feed.feed['link'] = "http://blogger-kcl.appspot.com/?blog_name=" + blog[0].name
        feed.feed['author'] = blog[0].authors[0]
        feed.feed['description'] = "This is a simple feed of blog " + blog[0].name
        
        feed.feed['guid'] = "123456789"
        feed.feed['blog_name'] = blog[0].name
        feed.feed['imgs_url'] = ['url1', 'url2', 'url3']

        for post in posts:
            item = {}
            item['guid'] = str(post.blog_key.urlsafe())
            item['title'] = post.title
            item['link'] = "http://blogger-kcl.appspot.com/?blog_name=" + blog[0].name + "&post_id=" + str(post.key.id())
            item['description'] = post.content
            item['imgs_url'] = post.imgs_url
            item['pubDate'] = time.mktime(post.create_time.timetuple())
            feed.items.append(item)

        rss = JINJA_ENVIRONMENT.get_template('rss.html')
        self.response.write(rss.render({
            'rssstr': feed.format_rss2_string()
        }))

application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/create', Createblog),
    ('/upload', Newpost),
    ('/viewpost', Viewpost),
    ('/rssgen', RSSGen),
], debug=True)

