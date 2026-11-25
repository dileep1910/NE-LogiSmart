
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegisterForm(UserCreationForm):

    ROLE_CHOICES = [
        ("USER", "USER"),
        ("ADMIN", "ADMIN"),
    ]

    email = forms.EmailField(required=True)

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        initial="USER",
        widget=forms.RadioSelect,
        label="Account Type",
    )

    admin_code = forms.CharField(
        required=False,
        label="Admin Registration Code",
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Enter admin verification code",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "role",
            "admin_code",
            "password1",
            "password2",
        ]

    def clean_email(self):
        email = self.cleaned_data.get("email")

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "This email is already registered."
            )

        return email

    def clean(self):
        cleaned_data = super().clean()

        role = cleaned_data.get("role")
        admin_code = cleaned_data.get("admin_code")

        if role == "ADMIN" and not admin_code:
            self.add_error(
                "admin_code",
                "Admin registration code is required."
            )

        return cleaned_data
