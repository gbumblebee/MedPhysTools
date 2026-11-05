"""Messages d'erreur pour l'utilisateur."""

from pydantic import ValidationError


def message_erreur(erreur: Exception) -> str:
    """Rend une erreur lisible : pydantic détaille trop pour un utilisateur."""
    if isinstance(erreur, ValidationError):
        return "; ".join(
            detail["msg"].removeprefix("Value error, ") for detail in erreur.errors()
        )
    return str(erreur.args[0]) if erreur.args else str(erreur)
