from feat import applications


class Featdjango(applications.Application):

    name = 'featdjango'
    version = '1.0'
    module_prefixes = ['featdjango']
    loadlist = ['featdjango.agent.agent',
                'featdjango.core.api',
                ]


featdjango = Featdjango()
