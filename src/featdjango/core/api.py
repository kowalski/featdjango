from zope.interface import implements

from feat.models import model, value, effect
from featdjango.application import featdjango

from feat.models.interface import IAspect


@featdjango.register_model
class Model(model.Model):

    model.identity('featdjango.server')
    model.attribute('a', value.String(), effect.static_value('a'))


class RootAspect(object):

    implements(IAspect)
    name = 'root'
    label = 'Worker gateway'
    desc = None
