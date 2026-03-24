from django.urls import include, path
from rest_framework import routers

from . import views

router = routers.DefaultRouter()
router.register('df-users', views.DFUserViewSet, basename='df-users')
router.register('ff-users', views.FilterFieldsUserViewSet, basename='ff-users')
router.register('ffcomplex-users',
                views.ComplexFilterFieldsUserViewSet,
                basename='ffcomplex-users')
router.register('users', views.UserViewSet)
router.register('notes', views.NoteViewSet)


urlpatterns = [
    path('', include(router.urls)),
]
