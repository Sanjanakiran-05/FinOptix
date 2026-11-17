from django.db import models
class RegisteredUser(models.Model):
    username = models.CharField(max_length=30, unique=True)
    password = models.CharField(max_length=128)
    contact = models.CharField(max_length=20)
    email = models.EmailField()
    address = models.TextField()

    def __str__(self):
        return self.username


class StockLibrary(models.Model):
    username = models.CharField(max_length=30)
    ticker = models.CharField(max_length=10)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['username', 'ticker'],
                name='unique_user_stock'
            )
        ]

    def __str__(self):
        return f"{self.username} - {self.ticker}"
