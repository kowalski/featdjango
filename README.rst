This is an integration layer between the django and twisted frameworks. If uses the webserver from feat (https://github.com/f3at/feat) to create a wsgi-like container for parsing django requests. Requests are being processed in a twisted threadpool, thanks to which a single server can processes more simultaneous requests than compared to a setup with apache + mod_wsgi.

Running in development
----------------------

To use this feat server you need to add *featdjango* to INSTALLED_APPS in your settings.py. The server is run with the following command: ::

  python manage.py server <port>

By default log output is processed as in any different django application. The log comming from the layer of webserver would be logged with *feat* category. You can direct them to the console and log file adding the following lines to you *settings.LOGGING['handlers']*: ::

   'feat': {
       'handlers': ['logfile', 'console'],
       'level': 'DEBUG',
       'propagate': True,
    },


Optionally you can get the feat logging completely separated to a different file by using the *--featlog* option. If you use it *FEAT_DEBUG* environment variable sets the log verbosity (1-ERROR, 2-WARNING, 3-INFO, 4-DEBUG, 5-LOG).


Features implemented:
---------------------

* Serves static files directly from twisted.

* Running server in development.

* Reloading of the code in development (by restarting the process).

* Process the requests in threads.

* Call methods returning Deferreds from inside the thread. The thread waits for the result.

* Make it possible to serve the script with the global prefix.

* Expose options to serve on HTTPS.

* Create a feat agent running an application of choice.

* Track when request goes away, cancel the processing when that happens.

TODO:
-----

* Track the statistics of how long the requests take to get processed and how long has the thread been sleeping due to waiting on the Deferreds.

* Optionally listen on unix socket (instead of TCP) and test it with the same kind of integration as gunicorn.


