import os
import sys
import re
import logging
from optparse import make_option

from featdjango.core import server, reloader

from feat.common import log
from feat.web import webserver

from django.core.management.base import BaseCommand, CommandError
from django.utils import autoreload

from twisted.internet import reactor

naiveip_re = re.compile(r"""^(?:
(?P<addr>
    (?P<ipv4>\d{1,3}(?:\.\d{1,3}){3}) |         # IPv4 address
    (?P<ipv6>\[[a-fA-F0-9:]+\]) |               # IPv6 address
    (?P<fqdn>[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*) # FQDN
):)?(?P<port>\d+)$""", re.X)
DEFAULT_PORT = "8000"
DEFAULT_ELF_FORMAT = ("time date cs-method cs-uri bytes time-taken c-ip "
                      "s-ip sc-status sc-comment cs-uri-stem cs-uri-query"
                      " sc(Content-Type) cs(Accept)")


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--featlog', action='store',
                    dest='featlog', default=None,
                    help='Log feat log a file (default: log to django log)'),
        make_option('--noreload', action='store_false', dest='use_reloader',
                    default=True,
                    help='Tells Django to NOT use the auto-reloader.'),
        make_option('--prefix', action='store', dest='prefix',
                    help='Run application under a prefix (example: "/myapp")'),
        make_option('--elflog_path', action='store', dest='elflog_path',
                    help='Specify to create an ELF log file.'),
        make_option('--elflog_fields', action='store', dest='elflog_fields',
                    help='Format of ELF log fields.',
                    default=DEFAULT_ELF_FORMAT),
        make_option('--stats', action='store', dest='stats_file',
                    help='Path to the file to store the worker statistics in'),
        make_option('--apiprefix', action='store', dest='apiprefix',
                    help='Prefix under which to host the server gateway api'),
        )


    # Validation is called explicitly each time the server is reloaded.
    requires_model_validation = False

    def handle(self, addrport='', *args, **options):
        if args:
            raise CommandError('Usage is server %s' % self.args)
        if not addrport:
            self.addr = ''
            self.port = DEFAULT_PORT
        else:
            m = re.match(naiveip_re, addrport)
            if m is None:
                raise CommandError('"%s" is not a valid port number '
                                   'or address:port pair.' % addrport)
            self.addr, _ipv4, _ipv6, _fqdn, self.port = m.groups()
            if not self.port.isdigit():
                raise CommandError(
                    "%r is not a valid port number." % self.port)
            if self.addr and _ipv6:
                raise CommandError("ipv6 is not supported")

        if not self.addr:
            self.addr = '127.0.0.1'

        if options.get('apiprefix') and not options.get('prefix'):
            raise CommandError("--apiprefix can only be used in conjuction "
                               "with --prefix. ")

        logger = logging.getLogger('feat')
        if options.get('featlog'):
            log.FluLogKeeper.init(options['featlog'])
            log.set_default(log.FluLogKeeper())
            log.info('featdjango', 'Use feat logging: %s' % (
                options['featlog'], ))
        else:
            log.set_default(log.PythonLogKeeper(logger))
            from feat.extern.log import log as flulog
            flulog.setPackageScrubList('featcredex', 'featdjango', 'feat')
            log.info('featdjango', 'Use python logging')

        log.info('featdjango', "Listening on %s:%s", self.addr, self.port)

        if os.environ.get("RUN_MAIN") == 'true':
            # this is how django autoreloader lets the child process know
            # that its a child process
            if options.get('elflog_path'):
                stats = webserver.ELFLog(options.get('elflog_path'),
                                         options.get('elflog_fields'))
            else:
                stats = None

            site = server.Server(self.addr, int(self.port),
                                 prefix=options.get('prefix'),
                                 apiprefix=options.get('apiprefix'),
                                 web_statistics=stats,
                                 thread_stats_file=options.get('stats_file'))
            reactor.callWhenRunning(site.initiate)
            reactor.addSystemEventTrigger('before', 'shutdown', site.cleanup)

            if options.get('use_reloader'):
                task = reloader.Reloader(reactor, site)
                reactor.callWhenRunning(task.run)

            reactor.run()

            if options.get('use_reloader'):
                if task.should_reload:
                    sys.exit(3)
        else:
            # in the original process we just spawn the child worker as
            # many times as it tells us to
            try:
                while autoreload.restart_with_reloader() == 3:
                    pass
            except KeyboardInterrupt:
                pass
