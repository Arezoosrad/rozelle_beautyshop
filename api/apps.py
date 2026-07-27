from django.apps import AppConfig
class FrontConfig(AppConfig):
	default_auto_field = 'django.db.models.BigAutoField'
	name = 'api'
	verbose_name = "api"

	def get_url_root(self):
		return ('api/v1/', 'api.urls', 'api')
	
	def ready(self):
		from .registry import model_registry
		model_registry.autodiscover()
		from .body_yar_access import install
		install()