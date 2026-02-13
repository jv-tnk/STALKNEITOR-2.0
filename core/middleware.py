class ProfileMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            request.profile = getattr(user, "perfil", None)
        else:
            request.profile = None
        return self.get_response(request)
