import base64
import binascii


class MousePkConverter:
    regex = r"[^/]+"
    prefix = "~b64~"

    def to_python(self, value):
        if not value.startswith(self.prefix):
            return value

        encoded_value = value[len(self.prefix):]
        padding = "=" * (-len(encoded_value) % 4)

        try:
            return base64.urlsafe_b64decode(
                f"{encoded_value}{padding}"
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return value

    def to_url(self, value):
        value = str(value)

        if "/" not in value and not value.startswith(self.prefix):
            return value

        encoded_value = base64.urlsafe_b64encode(
            value.encode("utf-8")
        ).decode("ascii").rstrip("=")

        return f"{self.prefix}{encoded_value}"
