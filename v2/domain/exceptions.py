class DomainException(Exception):
    """Excepción base para reglas del dominio."""
    pass

class ClientNotFoundException(DomainException):
    """El cliente especificado no existe."""
    pass

class InsufficientStockException(DomainException):
    """No hay cuentas disponibles en stock libre para la plataforma requerida."""
    pass

class PaymentAlreadyProcessedException(DomainException):
    """El pago pendiente ya ha sido aprobado o rechazado previamente."""
    pass

class InvalidHWIDException(DomainException):
    """El HWID proporcionado no es técnicamente válido."""
    pass
