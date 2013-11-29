import os
import sys
import mimetypes
import datetime
import time
from threading import Lock

from zope.interface import implements
from twisted.internet import reactor
from twisted.web.http import stringToDatetime

from django.core.handlers.base import BaseHandler
from django.core.urlresolvers import set_script_prefix
from django.http import HttpRequest, QueryDict
from django.contrib.staticfiles import finders
from django.utils import datastructures

from feat.web import webserver, http
from feat.common import defer, log, error
from feat.common.text_helper import format_block
from feat.gateway import resources

from feat.models.interface import IAspect

from featdjango.core import threadpool, stats, api


class FeatHandler(BaseHandler):

    initLock = Lock()

    def get_response(self, request):
        # Set up middleware if needed. We couldn't do this earlier, because
        # settings weren't available.
        if self._request_middleware is None:
            self.initLock.acquire()
            try:
                try:
                    # Check that middleware is still uninitialised.
                    if self._request_middleware is None:
                        self.load_middleware()
                except:
                    # Unload whatever middleware we got
                    self._request_middleware = None
                    raise
            finally:
                self.initLock.release()

        return BaseHandler.get_response(self, request)


class Server(webserver.Server):

    def __init__(self, hostname, port, server_name='',
                 log_keeper=None, prefix=None, interface='',
                 apiprefix=None, thread_stats_file=None, **kwargs):
        self.hostname = hostname

        self._prefix = prefix
        server_name = server_name or hostname
        if log_keeper is None:
            log_keeper = log.get_default()
        if thread_stats_file:
            self.thread_stats = stats.Statistics(log_keeper, thread_stats_file)
        else:
            self.thread_stats = None
        self.threadpool = threadpool.ThreadPool(
            logger=log_keeper, init_thread=self._init_thread,
            statistics=self.thread_stats)

        self.res = Root(self, server_name, prefix=prefix, apiprefix=apiprefix)
        webserver.Server.__init__(self, port, self.res, log_keeper=log_keeper,
                                  interface=interface, **kwargs)

    def initiate(self):
        self.threadpool.start()
        webserver.Server.initiate(self)
        if self.res._apiprefix:
            from feat.models import texthtml, applicationjson
            self.enable_mime_type(texthtml.MIME_TYPE)
            self.enable_mime_type(applicationjson.MIME_TYPE)
            self.enable_mime_type('image/png', 1)

    def cleanup(self):
        self.info('Shutting down.')
        d = defer.succeed(self)
        d.addCallback(webserver.Server.cleanup)
        d.addErrback(defer.inject_param, 1, error.handle_failure,
                     self, "Failure while shutting down webserver.")
        d.addCallback(defer.drop_param, self.threadpool.stop)
        d.addErrback(defer.inject_param, 1, error.handle_failure,
                     self, "Failure while stopping the threadpool.")
        return d

    def _init_thread(self):
        if self._prefix:
            set_script_prefix(self._prefix)


class FeatHttpRequest(HttpRequest):
    '''Adapter of feat.web.webserver.Request to the sublcass of
    django.http.HttpRequest which can be understood by djanbo BaseHandler.'''

    def __init__(self, request, location,
                 server_name='', server_port='', prefix=None):

        self._request = request

        self.path = '/'.join(location)

        if prefix:
            self.path_info = '/' + '/'.join(location[len(prefix) + 1:])
        else:
            self.path_info = self.path

        self.method = request.method.name
        self._server_name = server_name
        self._server_port = server_port

        # webserver.Request exposes a stream-like api (read and readline)
        # after first call of _get_raw_post_data() from the base class
        # this reference will point to StreamIO instance
        self._stream = request

        self._parse_meta()

        # this attribute is required by _load_post_and_files() method defined
        # in the base class
        self._read_started = False

        # added in 1.4
        # FIXME: really we should chain up to the parent's init
        #        the problem is we can't add our GET/POST/... properties when
        #        they are set as attributes in base __init__
        #        we could __init__, delete attributes, then set properties on
        #        the class
        self._post_parse_error = False

    def read(self, *args, **kwargs):
        self._read_started = True
        # feat would decode the string by default
        kwargs['decode'] = False
        return self._stream.read(*args, **kwargs)

    def _get_post(self):
        if not hasattr(self, '_post'):
            self._load_post_and_files()
        return self._post

    def _set_post(self, post):
        self._post = post

    def _get_get(self):
        if not hasattr(self, '_get'):
            self._get = QueryDict(self.META['QUERY_STRING'],
                                  encoding=self._request.encoding)
        return self._get

    def _set_get(self, get):
        self._get = get

    def _get_cookies(self):
        if not hasattr(self, '_cookies'):
            self._cookies = dict(self._request._ref.received_cookies)
        return self._cookies

    def _set_cookies(self, cookies):
        self._cookies = cookies

    def _get_files(self):
        if not hasattr(self, '_files'):
            self._load_post_and_files()
        return self._files

    def _get_request(self):
        if not hasattr(self, '_request_merged'):
            self._request_merged = datastructures.MergeDict(
                self.POST, self.GET)
        return self._request_merged

    POST = property(_get_post, _set_post)
    GET = property(_get_get, _set_get)
    COOKIES = property(_get_cookies, _set_cookies)
    FILES = property(_get_files)
    REQUEST = property(_get_request)

    ### private ###

    def _parse_meta(self):
        self.META = dict()

        headers = self._request._ref.requestHeaders
        for key, value in dict(headers.getAllRawHeaders()).iteritems():
            key = key.upper().replace('-', '_')
            if key not in ['CONTENT_LENGTH', 'CONTENT_TYPE']:
                key = 'HTTP_' + key
            self.META[key] = value[0]

        self.META['QUERY_STRING'] = http.compose_qs(self._request.arguments)
        self.META['REQUEST_METHOD'] = self.method
        self.META['REMOTE_HOST'] = self._request._ref.host.host
        self.META['REMOTE_ADDR'] = self._request._ref.client.host
        # FIXME: extract REMOTE_USER when authentication is done
        self.META['REMOTE_USER'] = ''
        self.META['SERVER_NAME'] = self._server_name
        self.META['SERVER_PORT'] = self._server_port


class PrefixMessage404(object):

    implements(webserver.IWebResource)

    def __init__(self, prefix):
        self._prefix = prefix

        self.authenticator = None
        self.authorizer = None

    def is_method_allowed(self, request, location, method):
        # method validation is performed by django
        return True

    def render_resource(self, request, response, location):
        response.set_mime_type('text/html')
        response.set_status(http.Status[404])
        response.write(format_block('''
        <html>
        <body>
        <h1>This server is configured to run under /%(prefix)s prefix.</h1>
        You should use <a href="/%(url)s">this url</a>.
        </body>
        </html>''') % dict(prefix='/'.join(self._prefix),
                           url="/".join(self._prefix + location[1:])))

    def locate_resource(self, request, location, remaining):
        return self

    def render_error(self, request, response, error):
        return error


class RootAspect(object):

    implements(IAspect)
    name = 'root'
    label = 'Worker gateway'
    desc = None


class Root(object):

    implements(webserver.IWebResource)

    def __init__(self, server, name, prefix=None, apiprefix=None):
        self.server = server
        self.authenticator = None
        self.authorizer = None

        self._handler = FeatHandler()

        # name is a remote hostname passed to the request.META
        self._name = name
        self._prefix = None
        if prefix:
            self._prefix = tuple(filter(None, prefix.split('/')))

        from django.conf import settings
        self._static_path = tuple(filter(None, settings.STATIC_URL.split('/')))
        self._static = Static(self.server)

        self._apiprefix = None
        if apiprefix:
            self._apiprefix = tuple(filter(None, apiprefix.split('/')))

        if self._apiprefix:
            self._api = api.Server(self.server)
            self._api.initiate(aspect=RootAspect())
            # to be created once we are listening thus knowing the port
            self._featstatic = None
            self._api_resource = None

    def set_inherited(self, authenticator=None, authorizer=None):
        self.authenticator = authenticator
        self.authorizer = authorizer

    def is_method_allowed(self, request, location, method):
        # method validation is performed by django
        return True

    def get_allowed_methods(self, request, location):
        # method validation is performed by django everything is allowed
        return http.Methods.values()

    def locate_resource(self, request, location, remaining):
        # this resource is handling all the requests
        l = len(self._static_path)
        if remaining[:l] == self._static_path:
            return self._static, remaining[l:]

        if (self._apiprefix and
            remaining[:len(self._prefix)] == self._apiprefix):
            if self._api_resource is None:
                self._api_resource = resources.ModelResource(
                    self._api, 'api',
                    names=[(request.domain.split(':')[0], self.server.port)],
                    )
                res = self._api_resource.initiate()
            else:
                res = self._api_resource

            return res, remaining[1:]

        # serve static files of feat from /static
        if self._apiprefix and remaining[0] == 'static':
            if not self._featstatic:
                from feat.configure import configure
                self._featstatic = resources.StaticResource(
                    request.domain.split(':')[0],
                    self.server.port, configure.gatewaydir)

            return self._featstatic, remaining[1:]

        if self._prefix and remaining[:len(self._prefix)] != self._prefix:
            return PrefixMessage404(self._prefix)
        return self

    def render_resource(self, request, response, location):
        django_request = FeatHttpRequest(
            request, location, self._name, self.server.port, self._prefix)
        job_explanation = "%s %s" % (django_request.method,
                                     django_request.get_full_path())
        d = self.server.threadpool.defer_to_thread(
            self._handler.get_response, django_request,
            job_explanation=job_explanation)
        d.addCallback(self._translate_response, response)
        return d

    def _translate_response(self, django_response, response):
        for header_name, header_value in django_response.items():
            response.set_header(header_name, header_value)
        for cookie_name, cookie in django_response.cookies.iteritems():
            # convert an http formated datetime to a datetime.datetime object
            expires = None
            if cookie['expires']:
                expires = datetime.datetime(*time.localtime(
                    stringToDatetime(cookie['expires']))[:6])
            max_age = cookie['max-age'] and int(cookie['max-age'])
            response.add_cookie(cookie_name, cookie.value,
                                expires=expires,
                                max_age=max_age,
                                domain=cookie['domain'],
                                path=cookie['path'],
                                secure=cookie['secure'])
        response.set_mime_type(django_response['Content-Type'])
        response.set_status(http.Status[django_response.status_code])
        response.write(django_response.content)

    def render_error(self, request, response, error):
        return error


class Static(object):

    implements(webserver.IWebResource)

    BUFFER_SIZE = 1024*1024*4

    def __init__(self, server):
        self.authenticator = None
        self.authorizer = None

        self._mime_types = mimetypes.MimeTypes()
        self.server = server

    ### IWebResource ###

    def set_inherited(self, authenticator=None, authorizer=None):
        self.authenticator = authenticator
        self.authorizer = authorizer

    def is_method_allowed(self, request, location, method):
        return method == http.Methods.GET

    def get_allowed_methods(self, request, location):
        return [http.Methods.GET]

    def locate_resource(self, request, location, remaining):
        # this will never be called
        request.context['path'] = '/'.join(filter(None, remaining))
        return self

    def render_resource(self, request, response, location):
        filepath = finders.find(request.context['path'])
        if not filepath:
            raise http.NotFoundError()

        if os.path.isdir(filepath):
            raise http.ForbiddenError()
        if not os.path.isfile(filepath):
            raise http.NotFoundError()

        rst = os.stat(filepath)
        age = time.time() - rst.st_mtime
        date_header = http.compose_datetime(time.time())
        response.set_header('date', date_header)
        response.set_header('expires', date_header)
        response.set_header('last-modified',
                            http.compose_datetime(rst.st_mtime))
        response.set_header('age', int(age))

        # FIXME: Caching Policy, should be extracted to a ICachingPolicy
        cache_control_header = request.get_header("cache-control") or ""
        pragma_header = request.get_header("pragma") or ""
        cache_control = http.parse_header_values(cache_control_header)
        pragma = http.parse_header_values(pragma_header)
        if not (u"no-cache" in cache_control or u"no-cache" in pragma):
            if u"max-age" in cache_control:
                max_age = int(cache_control[u"max-age"])
                if max_age == 0 or age < max_age:
                    response.set_status(http.Status.NOT_MODIFIED)
                    return

        length = rst.st_size
        mime_type, content_encoding = self._mime_types.guess_type(filepath)
        mime_type = mime_type or "application/octet-stream"

        response.set_length(length)
        response.set_mime_type(mime_type)
        response.set_header("connection", "close")
        if content_encoding is not None:
            response.set_header("content-encoding", content_encoding)

        try:
            res = open(filepath, "rb")
        except IOError:
            raise http.ForbiddenError(), None, sys.exc_info()[2]

        response.do_not_cache()

        expl = "%s %s" % (request.method.name, request.path)
        return self.server.threadpool.defer_to_thread(
            self._write_resource, response, res,
            job_explanation=expl)

    def render_error(self, request, response, error):
        return error

    ### private ###

    def _write_resource(self, response, res):

        try:
            while True:
                data = res.read(self.BUFFER_SIZE)
                if not data:
                    break
                reactor.callFromThread(response.write, data)
        finally:
            res.close()
