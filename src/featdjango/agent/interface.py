from zope.interface import Interface


class IServerFactory(Interface):

    def __call__(log_keeper, port, host_name=None,
                 prefix=None, interface='',
                 p12_path=None, check_client_cert=False,
                 server_stats=None):
        '''
        @type log_keeper:   L{ILogKeeper}
        @type server_stats: L{feat.web.webserver.IWebStatistics}

        @returns: L{IServer}
        '''


class IServer(Interface):

    def initiate():
        pass

    def cleanup():
        pass
