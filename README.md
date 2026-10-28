from django.db import models
from django.utils import timezone

class Invoice(models.Model):
    invoice_number = models.CharField(max_length=100)
    payment_date = models.DateField()
    payment_status = models.CharField(max_length=100)
    payment_method = models.CharField(max_length=100)
    payment_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_currency = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=100)
    payment_status = models.CharField(max_length=
