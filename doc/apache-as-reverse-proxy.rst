Production setup
----------------

This explains how to setup featdjango for production. Featdjango will be listening on a port on localhost, and we will use Apache as a reverse-proxy. Also our django application will run under a prefix, so that you can run different things on the same server under different prefixes.


Apache configuration
====================

The template for the configuration looks as follows:

  .. include:: apache-reverse-proxy.template
    :literal:

This configuration serves static files direcly from apache. You should set your *STATIC_ROOT* in *settings.py* to match the <Directory> directive.


Running featdjango from feat
============================

The template for running featdjango server from feat agent is as follows:

  .. include:: featdjango.ini.template
    :literal:

Using feat for running your server cover the monitoring part for you. Additionally it allows setting most of important options. This document doesn't cover the deployment of feat cluster itself. Someday I will explain it here as well.


Running featdjango from the command line
========================================

Alternatively you can run featdjango from command line and use Supervisor to monitor it for you. A good explanation of a simillar setup using gunicorn server can be found under the link: http://senko.net/en/django-nginx-gunicorn/.

The command line to run is: ::

  python manage.py server 127.0.0.1:PORT --prefix ${PREFIX} --noreload

The syntax is very much alike the *runserver* command. If you wish to server your application without the prefix, you can skip this option. Be sure to put *--noreload* though. It disables the mechanism restarting the server on a code change. This is nice in development, but certainly we don't want this to happen in production.


Important parts of settings.py
==============================

============
Static files
============

As mentioned before you'd probably want to have apache serve the static files for your application. To make this happen set the following: ::

  STATIC_ROOT = '/var/www/static-files-for-your-app'
  STATIC_URL = '/${PREFIX}/static/'

When you deploy be sure that the to run collectstatic, like this: ::

  python manage.py collectstatic


==============
Forwarded host
==============

Django needs to be told to use the *X-Forwarded-Host* host header the proxy provides. You do this by setting: ::

  USE_X_FORWARDED_HOST = True

=====
HTTPS
=====

If the apache server uses SSL certificate you should set HTTPS environment variable to "on". For example like this: ::

  export HTTPS=on

If your are using feat to run and monitor featdjango you can set it in the .ini file in *descriptor.environment*.
