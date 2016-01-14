from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login

from utilities.json_http import JsonResponse
from utilities.render import render

from wishlist.models import get_wishlist
from shop.views import get_recent_products
from checkout.models import Order
from cart.cart import get_cart
import shipping.util

from .models import Account
from .forms import AccountForm, UserForm, CreateUserForm


@login_required
@render('accounts/orders.html')
def orders(request):
    account = Account.objects.for_user(request.user)
    orders = Order.objects.filter(account=account)
    current = orders.filter(status=Order.STATUS_PAID) \
                    .order_by('created')
    completed = orders.filter(status=Order.STATUS_SHIPPED) \
                      .order_by('-created')

    return {
        'current': current,
        'completed': completed,
    }


@login_required
@render('accounts/details.html')
def details(request):
    account = Account.objects.for_user(request.user)

    if request.method == 'POST':
        account_form = AccountForm(request.POST, instance=account)
        user_form = UserForm(request.POST, instance=account.user)
        if account_form.is_valid() and user_form.is_valid():
            account_form.save()
            user_form.save()
            messages.info(request, 'Your details were saved')
            return redirect(details)
    else:
        account_form = AccountForm(instance=account)
        user_form = UserForm(instance=account.user)

    return {
        'account_form': account_form,
        'user_form': user_form,
    }


@render('accounts/create.html')
def create(request):
    shipping_opts = shipping.util.get_session(request)
    initial = {
        'country': shipping_opts.get('country'),
    }

    if request.method == 'POST':
        account_form = AccountForm(request.POST, initial=initial)
        user_form = CreateUserForm(request.POST)
        if account_form.is_valid() and user_form.is_valid():
            account = account_form.save(commit=False)
            account.user = user_form.save()
            account.save()
            auth_user = authenticate(
                username=account.user.email,
                password=user_form.cleaned_data['password1'])
            login(request, auth_user)
            messages.info(request, 'Your account was created.')
            return redirect(details)
    else:
        account_form = AccountForm(initial=initial)
        user_form = CreateUserForm()

    return {
        'account_form': account_form,
        'user_form': user_form,
    }


@render('accounts/recent.html')
def recent(request):
    return {
        'products': get_recent_products(request),
    }


def account_data(request):
    account = {}

    account['cart'] = get_cart(request).as_dict()

    if request.user.is_authenticated():
        account['user'] = {
            'first_name': request.user.first_name,
        }

    account['wishlist'] = get_wishlist(request).as_dict()

    return account


def account_data_view(request):
    return JsonResponse({'account': account_data(request)})
