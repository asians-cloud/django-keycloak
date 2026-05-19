from django.apps.config import AppConfig


class KeycloakAppConfig(AppConfig):
    name = 'django_keycloak'
    verbose_name = 'Keycloak'
    default_auto_field = 'django.db.models.AutoField'
