from django.contrib import admin
from django.urls import path, include
from api.views import api_root # Import the new welcome view

# Import settings and static to serve files when DEBUG is False
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    ## THIS IS THE NEW LINE THAT WE ADDED
    # It points the homepage ('') to our new api_root view
    path('', api_root, name='api-root'),
    # This is the URL for the built-in Django admin page.
    path('admin/', admin.site.urls),
    # This line connects your main project to your API app's URLs,
    # so paths like '/api/shipments/' will work.
    path('api/', include('api.urls')),
]

# This is the crucial part for serving static files locally when DEBUG is False.
# It should ONLY be used in a development environment for testing.
if not settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
