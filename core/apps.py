from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = "core"

    app_title = "barber"
    app_logo_url = "/static/images/logo.png"

    def get_url_root(self):
        return ('', 'core.urls', 'core')
