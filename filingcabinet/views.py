import json

from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView
from django.db.models import Q
from django.utils.translation import ugettext as _

from . import get_document_model, get_documentcollection_model
from .api_views import PageSerializer

Document = get_document_model()
DocumentCollection = get_documentcollection_model()


class PkSlugMixin:
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.slug != self.kwargs.get('slug', ''):
            return redirect(self.object)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)


class AuthMixin:
    def get_queryset(self):
        qs = super().get_queryset()
        cond = Q(public=True)
        if self.request.user.is_authenticated:
            if self.request.user.is_superuser:
                return qs
            cond |= Q(user=self.request.user)
        return qs.filter(cond)


def get_js_config(request):
    return {
        'urls': {
            'pageApiUrl': reverse('api:page-list'),
            'pageAnnotationApiUrl': reverse('api:pageannotation-list'),
        },
        'i18n': {
            'page': _('page'),
            'pages': _('pages'),
            'matches': _('matches'),
            'search': _('Search'),
            'searching': _('Searching...'),
            'found_on': _('Found on'),
        }
    }


class DocumentView(AuthMixin, PkSlugMixin, DetailView):
    model = Document
    PREVIEW_PAGE_COUNT = 10

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        num_pages = self.object.num_pages
        try:
            start_from = int(self.request.GET.get('page', 1))
            if start_from > num_pages:
                raise ValueError
        except ValueError:
            start_from = 1
        pages = self.object.page_set.all()
        pages = pages.filter(number__gte=start_from)[:self.PREVIEW_PAGE_COUNT]
        ctx['pages'] = pages
        ctx['beta'] = self.request.GET.get('beta') is not None
        serializer_klass = self.object.get_serializer_class()
        api_ctx = {
            'request': self.request
        }
        data = serializer_klass(self.object, context=api_ctx).data
        data['pages'] = PageSerializer(pages, many=True, context=api_ctx).data
        ctx['page'] = start_from
        ctx['document_data'] = json.dumps(data)
        ctx['config'] = json.dumps(get_js_config(self.request))
        return ctx


class DocumentCollectionView(AuthMixin, PkSlugMixin, DetailView):
    model = DocumentCollection

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.slug and self.kwargs.get('slug') is None:
            return redirect(self.object)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['documents'] = self.object.documents.all()
        serializer_klass = self.object.get_serializer_class()
        api_ctx = {
            'request': self.request
        }
        data = serializer_klass(self.object, context=api_ctx).data
        context['documentcollection_data'] = json.dumps(data)
        context['config'] = json.dumps(get_js_config(self.request))

        return context
