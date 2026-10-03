from dataclasses import dataclass
@dataclass(frozen=True)
class ApiModelConfig:
    model: object
    readable_fields: set[str]
    writable_fields: set[str]
    lookup_field: str="id"
    search_fields: set[str]|None=None
    ordering_fields: set[str]|None=None
class ApiRegistry:
    def __init__(self): self._items={}
    def register(self,name,model,readable_fields,writable_fields=(),lookup_field="id",search_fields=None,ordering_fields=None):
        if name in self._items: raise ValueError(f"API model already registered: {name}")
        self._items[name]=ApiModelConfig(model,set(readable_fields),set(writable_fields),lookup_field,set(search_fields) if search_fields else None,set(ordering_fields) if ordering_fields else None)
    def get(self,name):
        if name not in self._items: raise LookupError(f"API model is not registered: {name}")
        return self._items[name]
model_registry=ApiRegistry()
