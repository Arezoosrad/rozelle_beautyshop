from .models import Shipment
def create_shipment(*,order): return Shipment.objects.get_or_create(order=order)[0]
