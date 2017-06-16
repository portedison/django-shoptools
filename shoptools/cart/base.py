from datetime import datetime
import decimal

from django.contrib.postgres.fields import JSONField
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey

from .util import get_cart_html, get_shipping_module, get_vouchers_module, \
    validate_options


# TODO
# the SessionCart and the Order object should share an interface
# so should SessionCartLine and OrderLine
# in a template we should be able to treat a db-saved or session "cart"
# exactly the same
# BUT are interfaces "pythonic"? Should these be ABCs?

# Rather than raising NotImplementedError for things like total and
# shipping_cost, we should check using hasattr and ignore if they're not there


class ICart(object):
    """Define interface for "cart" objects, which may be a session-based
       "cart" or a db-saved "order".

       Subclasses should implement the following:

           update_quantity(self, ctype, pk, quantity)
           clear(self)
           count(self)
           get_lines(self)

       and may implement the following (optional):

           subtotal
           total
           get_shipping_options
           set_shipping_options
           get_voucher_codes

       """

    def add(self, ctype, pk, quantity=1, options={}):
        return self.update_quantity(ctype, pk, quantity, add=True,
                                    options=options)

    def remove(self, ctype, pk, options={}):
        return self.update_quantity(ctype, pk, 0, options=options)

    def get_errors(self):
        """Validate each cart line item. Subclasses may override this method
           to perform whole-cart validation. Return a list of error strings
        """

        errors = []
        for line in self.get_lines():
            errors += line.get_errors()

        errors += self.shipping_errors()

        return errors

    @property
    def is_valid(self):
        return self.count() and not self.get_errors()

    def as_dict(self):
        """Return dict of data for this cart instance, for json serialization.
           subclasses should override this method. """

        data = {
            'count': self.count(),
            'lines': [line.as_dict() for line in self.get_lines()],
        }

        if hasattr(self, 'get_shipping_options'):
            data['shipping_options'] = self.get_shipping_options()

        for f in ('subtotal', 'total'):
            if hasattr(self, f):
                attr = getattr(self, f)
                data[f] = float(attr) if attr is not None else None

        # TODO add discounts?

        data['html_snippet'] = get_cart_html(self)

        return data

    def update_quantity(self, ctype, pk, quantity, options={}):
        raise NotImplementedError()

    def get_lines(self):
        """Return all valid lines (i.e. exclude those where the item is
           not valid or deleted) """
        raise NotImplementedError()

    def count(self):
        """Sum the quantities of all valid lines. """
        raise NotImplementedError()

    def clear(self):
        """Delete all cart lines. """
        raise NotImplementedError()

    # def set_shipping_options(self, options):
    #     """Save the provided options
    #
    #     Assume the options have already been validated, if necessary.
    #     """
    #
    #     raise NotImplementedError()
    #
    # def get_shipping_options(self):
    #     """Get shipping options, if any. """
    #
    #     raise NotImplementedError()

    @property
    def shipping_cost(self):
        shipping_module = get_shipping_module()
        if shipping_module:
            return shipping_module.calculate(self)
        return 0

    def shipping_errors(self):
        shipping_module = get_shipping_module()
        if shipping_module:
            return shipping_module.get_errors(self)
        return []

    # TODO tidy up discount stuff - does it belong here?
    def calculate_discounts(self, include_shipping=True):
        voucher_module = get_vouchers_module()
        if voucher_module:
            return voucher_module.calculate_discounts(
                self, self.get_voucher_codes(),
                include_shipping=include_shipping)
        return ([], None)

    @property
    def total_discount(self):
        discounts, invalid = self.calculate_discounts()
        return sum(d.amount for d in discounts)

    def save_to(self, obj):
        from .base import BaseOrder
        assert isinstance(obj, BaseOrder)

        [l.delete() for l in obj.get_lines()]
        for cart_line in self.get_lines():
            line = obj.get_line_cls()()
            line.parent_object = obj
            line.item = cart_line.item
            line.options = cart_line.options
            line.quantity = cart_line.quantity
            # line.currency = self.currency
            line.save()

        # save shipping info - cost calculated automatically
        if hasattr(obj, 'set_shipping_options'):
            # No need to validate options here, as was done when they were
            # saved to self.
            obj.set_shipping_options(self.get_shipping_options())

        # save valid discounts - TODO should this go here?
        # Do we need to subclass Cart as DiscountCart?
        from shoptools.checkout.models import Order
        if isinstance(obj, Order):
            voucher_module = get_vouchers_module()
            vouchers = self.get_voucher_codes() if voucher_module else None
            if vouchers:
                [d.delete() for d in obj.discount_set.all()]
                voucher_module.save_discounts(obj, vouchers)


class IShippable(object):
    # TODO - maybe move shipping stuff in here?
    pass


class ICartLine(object):
    """Define interface for cart lines, which are attached to an ICart.
       Subclasses must implement the following properties

       item
       quantity
       total
       description
       parent_object

    """

    def get_errors(self):
        """Validate this line's item. Return a list of error strings"""
        return self.item.cart_errors(self) if self.item else []

    @property
    def ctype(self):
        # app.model, compatible with the ctype argument to Cart.update etc
        return '%s.%s' % (self.item._meta.app_label,
                          self.item._meta.model_name)

    # @property
    # def item_id(self):
    #     return self.item.pk

    # @property
    # def unique_identifier(self):
    #     # unique across all models, intended for use as an id or class
    #     # attribute where the ability to get a specific item is required
    #     return self.ctype.replace('.', '-') + '-%d' % self.item.id

    def options_text(self):
        # TODO handle the case where options is blank i.e. ''
        return ', '.join('%s: %s' % opt for opt in self.options.items())

    def as_dict(self):
        return {
            'description': self.description,
            'options': self.options,
            'quantity': self.quantity,
            'total': float(self.total),
            # 'unique_identifier':
            #     self.unique_identifier if self.item else None,
        }


class ICartItem(object):
    """Define interface for objects which may be added to a cart.

    Subclasses must define cart_line_total; other methods are optional"""

    def cart_line_total(self, line):
        """Returns the total price for quantity of this item. """

        # currently must return a float/int, not decimal, due to django's
        # serialization limitations - see
        # https://docs.djangoproject.com/en/1.8/topics/http/sessions/#session-serialization
        raise NotImplementedError()

    def purchase(self, line):
        """Called on successful purchase. """
        pass

    def cart_errors(self, line):
        """Used by the cart and checkout to check for errors, i.e. out of
           stock. """
        return []

    def cart_description(self):
        """Describes the item in the checkout admin. Needed because the needs
           to store a description of the item as purchased, even if it is
           deleted or changed down the track. """
        return str(self)

    def available_options(self):
        """Available purchase options for the product - product variants
           should generally be preferred over this system. Use options where
           it doesn't make sense for the different option to be represented
           by a different instance, i.e. "Add monogramming", or need to be
           ad-hoc, i.e. specific to the product instance rather than
           the model.

           NOTE values must be strings because that's what gets posted through
           from forms. TODO fix this. Do we enforce json payloads from the
           frontend?

           Example:

           {
               'color': ['red', 'black'],
               'size': ['S', 'M', 'L'],
           }
        """

        return {}

    def default_options(self):
        """Return a dict of defaults, by default this just takes the first
           option from each. """

        return dict((key, opts[0]) for key, opts in self.available_options())

    @property
    def ctype(self):
        # app.model, compatible with the ctype argument to Cart.add etc
        return '%s.%s' % (self._meta.app_label,
                          self._meta.model_name)

    # @property
    # def unique_identifier(self):
    #     # unique across all models, intended for use as an id or class
    #     # attribute where the ability to get a specific item is required
    #     return self.ctype.replace('.', '-') + '-%d' % self.id


# Abstract models


class BaseOrder(models.Model, ICart):
    """Base class for "Order" models, which are the db-saved version of a
       SessionCart. Theoretically, this model can be used interchangeably with
       the SessionCart, adding/removing items etc. """

    class Meta:
        abstract = True

    def update_quantity(self, ctype, pk, quantity=1, add=False, options={}):
        # TODO should validation happen here? Should probably be a separate
        # layer, and this function should assume valid input (ref django's
        # Model.save and Model.clean)

        # TODO purge 'add' argument
        if quantity == 0 and add:
            return (False, 'No quantity specified')

        if not self.pk:
            # must be an unsaved instance; assume that it's ready to be saved
            self.save()

        line = self.get_line(ctype, pk, options, create=True)

        # quantity may be an addition or a straight update
        if add:
            line.quantity = (line.quantity or 0) + quantity
        else:
            line.quantity = quantity

        # purge if quantity is zero after the update
        if not line.quantity:
            # may have been created on the fly if it didn't exist
            if line.pk:
                line.delete()
            return (True, None)

        # verify the order line object before saving
        errors = line.get_errors()
        if errors:
            return (False, errors)

        line.save()
        return (True, None)

    def get_line(self, ctype, pk, options, create=False):
        """This method should always be used to get a line, rather than
           directly via orm. """

        app_label, model = ctype.split('.')
        ctype_obj = ContentType.objects.get(app_label=app_label, model=model)
        lines = self.get_line_cls().objects.filter(parent_object=self)
        options = validate_options(ctype, pk, options)
        lookup = {
            'parent_object': self,
            # 'item_content_type__app_label': app_label,
            # 'item_content_type__model': model,
            'item_content_type': ctype_obj,
            'item_object_id': pk,
            'options': options,
        }
        try:
            line = lines.get(**lookup)
        except self.get_line_cls().DoesNotExist:
            if not create:
                return None
            line = self.get_line_cls()(**lookup)

        # TODO think of a better way to solve this problem - saved orders
        # need to be decoupled from the request somehow. Save region etc
        # to the db in a json field?
        line.parent_object = self
        return line

    def get_lines(self):
        """This method should always be used to get lines, rather than
           directly via orm. """

        lines = self.get_line_cls().objects.filter(parent_object=self) \
            .order_by('pk')
        for line in lines:
            if line.item:
                # parent_object may have been instantiated with a request,
                # so attach it to the line
                line.parent_object = self
                yield line

    def empty(self):
        return not self.count()

    def count(self):
        return sum(line.quantity for line in self.get_lines())

    def clear(self):
        # this doesn't have to delete the Order, it could hang around and
        # the lines could just be deleted
        # if we ever want to save details from one order to the next (shipping
        # options for example) do it here

        # return self.get_lines().delete()
        self.delete()

    @property
    def subtotal(self):
        return decimal.Decimal(sum(line.total for line in self.get_lines()))


class BaseOrderLine(models.Model, ICartLine):
    """An OrderLine is the db-persisted version of a Cart line, created by
    subclassing this model. It implements the same ICartLine interface as the
    cart lines, and is intended to be attached to an object you provide via
    a parent_object ForeignKey. Your object can be thought of as an Order,
    although it doesn't have to be; it could equally support a "save this cart
    for later" feature.

    Subclasses must implement a parent_object ForeignKey
    """

    # item object *must* support ICartItem
    item_content_type = models.ForeignKey(ContentType,
                                          on_delete=models.PROTECT)
    item_object_id = models.PositiveIntegerField()
    item = GenericForeignKey('item_content_type', 'item_object_id')

    created = models.DateTimeField(default=datetime.now)
    quantity = models.IntegerField()
    options = JSONField(default=dict, blank=True)

    # currency = models.CharField(max_length=3, editable=False,
    #    default=DEFAULT_CURRENCY)
    # total = models.DecimalField(max_digits=8, decimal_places=2)
    # description = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True
        # NOTE I'm relying on the jsonfield ordering its keys consistently
        unique_together = ('item_content_type', 'item_object_id',
                           'parent_object', 'options')

    @property
    def total(self):
        if not self.item:
            return decimal.Decimal(0)
        return decimal.Decimal(
            self.item.cart_line_total(self))

    @property
    def description(self):
        if not self.item:
            return ''
        return self.item.cart_description()

    def __str__(self):
        return "%s x %s: $%.2f" % (self.description, self.quantity,
                                   self.total)