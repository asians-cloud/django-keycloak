from unittest import mock

from django.test import TestCase, RequestFactory, override_settings

from django_keycloak.middleware import (
    KeycloakStatelessBearerAuthenticationMiddleware,
)


@override_settings(KEYCLOAK_BEARER_AUTHENTICATION_EXEMPT_PATHS=[])
class StatelessBearerMalformedTokenTestCase(TestCase):
    """AC-1600.

    The stateless bearer middleware runs *before* DRF, so any unhandled error
    while parsing the ``Authorization`` header / token surfaces as a 500. A
    malformed or invalid token must instead leave the request unauthenticated
    so the DRF auth layer can return a proper 401 ("Invalid token.").
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = KeycloakStatelessBearerAuthenticationMiddleware(
            get_response=lambda request: request)

    def test_bearer_header_without_token_does_not_500(self):
        # "Authorization: Bearer" with no token used to IndexError on
        # split(' ')[1]. Now it returns early, unauthenticated.
        request = self.factory.get('/', HTTP_AUTHORIZATION='Bearer')

        self.middleware.process_request(request)  # must not raise

        self.assertFalse(getattr(request, 'user', None))

    def test_empty_bearer_token_does_not_500(self):
        request = self.factory.get('/', HTTP_AUTHORIZATION='Bearer ')

        self.middleware.process_request(request)  # must not raise

        self.assertFalse(getattr(request, 'user', None))

    def test_authenticate_error_is_swallowed_not_propagated(self):
        # Any error raised while authenticating a malformed token must be
        # swallowed by the middleware (treated as unauthenticated), never a 500.
        request = self.factory.get('/', HTTP_AUTHORIZATION='Bearer not.a.jwt')

        with mock.patch('django_keycloak.middleware.authenticate',
                        side_effect=ValueError('malformed token')):
            self.middleware.process_request(request)  # must not raise

        self.assertFalse(getattr(request, 'user', None))

    def test_valid_token_still_authenticates(self):
        # A token that authenticates successfully still sets request.user.
        sentinel_user = object()
        request = self.factory.get('/', HTTP_AUTHORIZATION='Bearer good.token')

        with mock.patch('django_keycloak.middleware.authenticate',
                        return_value=sentinel_user):
            self.middleware.process_request(request)

        self.assertIs(request.user, sentinel_user)
