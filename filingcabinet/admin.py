from django.contrib import admin
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from .models import Page, CollectionDocument


class PageInline(admin.StackedInline):
    model = Page
    raw_id_fields = ('document',)


class DocumentBaseAdmin(admin.ModelAdmin):
    inlines = [PageInline]
    save_on_top = True
    search_fields = ('title',)
    date_hierarchy = 'created_at'
    list_display = ('title', 'created_at', 'num_pages', 'public', 'pending')
    list_filter = ('pending', 'public', 'allow_annotation',)
    raw_id_fields = ('user',)
    readonly_fields = ('uid',)
    prepopulated_fields = {'slug': ('title',)}
    actions = ['process_document', 'reprocess_document', 'fix_document_paths']

    def get_inline_instances(self, request, obj=None):
        ''' Only show inline for docs with fewer than 31 pages'''
        if obj is not None and obj.num_pages and obj.num_pages <= 30:
            return super().get_inline_instances(request, obj=obj)
        return []

    def save_model(self, request, doc, form, change):
        doc.updated_at = timezone.now()
        super().save_model(request, doc, form, change)
        if not change:
            doc.process_document()

    def process_document(self, request, queryset):
        for instance in queryset:
            instance.process_document(reprocess=False)
        self.message_user(request, _("Started processing documents."))
    process_document.short_description = _("Process documents")

    def reprocess_document(self, request, queryset):
        for instance in queryset:
            instance.process_document(reprocess=True)
        self.message_user(request, _("Started reprocessing documents."))
    reprocess_document.short_description = _("Reprocess documents")

    def fix_document_paths(self, request, queryset):
        from .tasks import files_moved_task

        for instance in queryset:
            files_moved_task.delay(instance.id)

        self.message_user(request, _("Fixing document paths..."))
    fix_document_paths.short_description = _("Fix document paths")


class PageAdmin(admin.ModelAdmin):
    raw_id_fields = ('document',)
    search_fields = ('document__title',)
    list_filter = ('pending', 'corrected', 'number',)
    list_display = ('show_title', 'number', 'pending', 'corrected')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.select_related('document')
        return qs

    def show_title(self, obj):
        return obj.document.title


class PageAnnotationAdmin(admin.ModelAdmin):
    raw_id_fields = ('user', 'page',)


class DocumentInline(admin.StackedInline):
    model = CollectionDocument
    raw_id_fields = ('document',)


class DocumentCollectionBaseAdmin(admin.ModelAdmin):
    raw_id_fields = ('user', 'documents',)
    inlines = [DocumentInline]
    save_on_top = True
    search_fields = ('title',)
    date_hierarchy = 'created_at'
    list_display = ('title', 'created_at', 'public', 'user')
    prepopulated_fields = {'slug': ('title',)}

    def get_inline_instances(self, request, obj=None):
        ''' Only show inline for docs with fewer than 31 pages'''
        if obj is not None:
            doc_count = obj.documents.count()
            if doc_count < 31:
                return super().get_inline_instances(request, obj=obj)
        return []


class CollectionDocumentBaseAdmin(admin.ModelAdmin):
    list_display = ('document', 'collection', 'order')
    raw_id_fields = ('document', 'collection',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.select_related('document', 'collection')
        return qs


# Register them yourself
# admin.site.register(Document, DocumentAdmin)
# admin.site.register(Page)
