import re
import logging
from optparse import make_option

from featdjango.core import server, reloader

from feat.common import log

from django.core.management.base import BaseCommand, CommandError

from twisted.internet import reactor

naiveip_re = re.compile(r"""^(?:
(?P<addr>
    (?P<ipv4>\d{1,3}(?:\.\d{1,3}){3}) |         # IPv4 address
    (?P<ipv6>\[[a-fA-F0-9:]+\]) |               # IPv6 address
    (?P<fqdn>[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*) # FQDN
):)?(?P<port>\d+)$""", re.X)
DEFAULT_PORT = "8000"


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--featlog', action='store',
                    dest='featlog', default=None,
                    help='Log feat log a file (default: log to django log)'),
        make_option('--noreload', action='store_false', dest='use_reloader',
                    default=True,
                    help='Tells Django to NOT use the auto-reloader.'))

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

        logger = logging.getLogger('feat')
        if options.get('featlog'):
            log.FluLogKeeper.init(options['featlog'])
        else:
            log.set_default(log.PythonLogKeeper(logger))

        log.info('feat', "Listening on %s:%s", self.addr, self.port)

        site = server.Server(self.addr, int(self.port))
        reactor.callWhenRunning(site.initiate)
        if options.get('use_reloader'):
            task = reloader.Reloader(reactor, site)
            reactor.callWhenRunning(task.run)
        try:
            reactor.run()
        except KeyboardInterrupt:
            pass
