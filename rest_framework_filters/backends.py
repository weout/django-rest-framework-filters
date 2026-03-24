import warnings
from contextlib import contextmanager
from importlib.util import find_spec

from django.apps import apps
from django import forms
from django.http import QueryDict
from django_filters.rest_framework import backends
from rest_framework.exceptions import ValidationError

from .complex_ops import combine_complex_queryset, decode_complex_ops
from .filterset import FilterSet


class RestFrameworkFilterBackend(backends.DjangoFilterBackend):
    filterset_base = FilterSet

    @property
    def template(self):
        if find_spec('crispy_forms') is not None and apps.is_installed('crispy_forms'):
            return 'rest_framework_filters/crispy_form.html'
        return 'rest_framework_filters/form.html'

    @contextmanager
    def patch_for_rendering(self, request):
        # Patch ``.get_filterset_class()`` so the resulting filterset does not perform
        # filter expansion during form rendering.
        original = self.get_filterset_class

        def get_filterset_class(view, queryset=None):
            filterset_class = original(view, queryset)

            # Don't break if filterset_class is not provided
            if filterset_class is None:
                return None

            # django-filter compatibility
            if issubclass(filterset_class, FilterSet):
                filterset_class = filterset_class.disable_subset(depth=1)

            return filterset_class

        self.get_filterset_class = get_filterset_class
        try:
            yield
        finally:
            self.get_filterset_class = original

    def to_html(self, request, queryset, view):
        # Patching the behavior of ``.get_filterset_class()`` in this method allows us
        # to avoid maintenance issues with code duplication.
        with self.patch_for_rendering(request):
            return super().to_html(request, queryset, view)

    def get_schema_fields(self, view):
        warnings.warn(
            '`get_schema_fields()` is deprecated; DRF now uses '
            '`get_schema_operation_parameters()` for OpenAPI schema generation.',
            DeprecationWarning,
            stacklevel=2,
        )
        return []

    def get_schema_operation_parameters(self, view):
        try:
            queryset = getattr(view, 'queryset', None)
            if queryset is None:
                queryset = view.get_queryset()
            filterset_class = self.get_filterset_class(view, queryset)
        except Exception:
            warnings.warn(
                '{} is not compatible with schema generation'.format(view.__class__)
            )
            return []

        if not filterset_class:
            return []

        filters_map = getattr(
            filterset_class,
            'expanded_filters',
            filterset_class.base_filters,
        )
        return [
            self._build_parameter(field_name, field)
            for field_name, field in filters_map.items()
        ]

    def _build_parameter(self, field_name, field):
        schema = self._get_schema_for_filter(field)
        parameter = {
            'name': field_name,
            'in': 'query',
            'required': bool(field.extra.get('required', False)),
            'schema': schema,
        }

        label = getattr(field, 'label', None)
        if label:
            parameter['description'] = str(label)

        if schema.get('type') == 'array':
            parameter['style'] = 'form'
            parameter['explode'] = True

        return parameter

    def _get_schema_for_filter(self, filter_field):
        form_field = filter_field.field
        schema = {'type': 'string'}
        null_boolean_field = getattr(forms, 'NullBooleanField', None)

        if null_boolean_field and isinstance(form_field, null_boolean_field):
            return {'type': 'boolean'}
        if isinstance(form_field, forms.BooleanField):
            return {'type': 'boolean'}
        if isinstance(form_field, forms.IntegerField):
            return {'type': 'integer'}
        if isinstance(form_field, (forms.DecimalField, forms.FloatField)):
            return {'type': 'number'}
        if isinstance(form_field, forms.DateTimeField):
            return {'type': 'string', 'format': 'date-time'}
        if isinstance(form_field, forms.DateField):
            return {'type': 'string', 'format': 'date'}
        if isinstance(form_field, forms.TimeField):
            return {'type': 'string', 'format': 'time'}
        if isinstance(form_field, forms.UUIDField):
            return {'type': 'string', 'format': 'uuid'}
        if isinstance(
            form_field,
            (
                forms.MultipleChoiceField,
                forms.TypedMultipleChoiceField,
                forms.ModelMultipleChoiceField,
            ),
        ):
            choices = self._get_choices(form_field)
            items = {'type': 'string'}
            if choices:
                items['enum'] = choices
            return {'type': 'array', 'items': items}

        choices = self._get_choices(form_field)
        if choices:
            schema['enum'] = choices

        return schema

    def _get_choices(self, form_field):
        choices = [
            value for value, _ in getattr(form_field, 'choices', [])
            if value not in ('', None)
        ]
        return choices or None


class ComplexFilterBackend(RestFrameworkFilterBackend):
    complex_filter_param = 'filters'
    operators = None
    negation = True

    def filter_queryset(self, request, queryset, view):
        if self.complex_filter_param not in request.query_params:
            return super().filter_queryset(request, queryset, view)

        # Decode the set of complex operations
        encoded_querystring = request.query_params[self.complex_filter_param]
        try:
            complex_ops = decode_complex_ops(
                encoded_querystring,
                self.operators,
                self.negation,
            )
        except ValidationError as exc:
            raise ValidationError({self.complex_filter_param: exc.detail})

        # Collect the individual filtered querysets
        querystrings = [op.querystring for op in complex_ops]
        try:
            querysets = self.get_filtered_querysets(querystrings, request, queryset, view)
        except ValidationError as exc:
            raise ValidationError({self.complex_filter_param: exc.detail})

        return combine_complex_queryset(querysets, complex_ops)

    def get_filtered_querysets(self, querystrings, request, queryset, view):
        original_GET = request._request.GET

        querysets, errors = [], {}
        for qs in querystrings:
            request._request.GET = QueryDict(qs)
            try:
                result = super().filter_queryset(request, queryset, view)
                querysets.append(result)
            except ValidationError as exc:
                errors[qs] = exc.detail
            finally:
                request._request.GET = original_GET

        if errors:
            raise ValidationError(errors)
        return querysets
