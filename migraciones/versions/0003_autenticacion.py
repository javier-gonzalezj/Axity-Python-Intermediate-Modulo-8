"""Agrega rol y password_hash a usuarios (autenticación con JWT)

Los usuarios que ya existen quedan como "cliente" y sin contraseña: no pueden
iniciar sesión hasta que se les asigne una (ver api/crear_admin.py).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usuarios") as batch_op:
        # server_default: las filas existentes reciben 'cliente' (la columna es NOT NULL)
        batch_op.add_column(
            sa.Column("rol", sa.String(length=20), nullable=False, server_default="cliente")
        )
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usuarios") as batch_op:
        batch_op.drop_column("password_hash")
        batch_op.drop_column("rol")
