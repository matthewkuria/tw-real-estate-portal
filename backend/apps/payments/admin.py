from django.contrib import admin

# Register your models here.
from .models import Payment
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
   pass

   
