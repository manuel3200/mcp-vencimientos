from pydantic import BaseModel, Field

class ItemCuentaLote(BaseModel):
    plataforma: str = Field(description="Plataforma (Netflix, Disney+, Max, etc.)")
    correo: str = Field(description="Correo o usuario de la cuenta")
    contrasena: str = Field(description="Contraseña de la cuenta")
    fecha_vencimiento: str = Field(description="Fecha de vencimiento formato YYYY-MM-DD")
    precio: str = Field(default="", description="Precio cobrado al cliente")
    recurrencia: str = Field(default="mensual", description="mensual, trimestral, anual, unico")
    perfil: str = Field(default="", description="Nombre de perfil si aplica")
    pin: str = Field(default="", description="PIN del perfil si aplica")
