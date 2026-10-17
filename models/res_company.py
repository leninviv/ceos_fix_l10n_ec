# -*- coding: utf-8 -*-
from odoo import fields, models


CEOS_EDI_MODE_SELECTION = [
    ("1", "Opción 1: Generar XML y autorizar en SRI (Odoo)"),
    ("2", "Opción 2: Generar XML y dejarlo en directorio"),
    ("3", "Opción 3: No generar XML (solo contabilidad)"),
]

# RUC del proveedor del sistema (software) para infoAdicional SRI.
# Vacío a propósito: cada instalación lo define en Contabilidad → Ajustes → CEOS EDI
# (así el módulo no lleva hardcodeado el RUC de quien lo vende).
CEOS_EDI_RUC_PROVEEDOR = ""
CEOS_EDI_RUC_PROVEEDOR_LABEL = "RUC Proveedor"


class ResCompany(models.Model):
    _inherit = "res.company"

    ceos_next_withhold_sale = fields.Char(
        string="Siguiente retención venta (sugerida)",
        help="Usado por CEOS Secuencias / wizard de retención. "
        "Las retenciones de venta en Odoo son siempre manuales.",
    )

    ceos_edi_additional_ruc_proveedor = fields.Char(
        string="RUC Proveedor (info adicional XML)",
        default=CEOS_EDI_RUC_PROVEEDOR,
        help="Se agrega en infoAdicional de factura, NC, ND, retención, "
        "liquidación de compra y guía de remisión: "
        '<campoAdicional nombre="RUC Proveedor">…</campoAdicional>. '
        "Corresponde al RUC del proveedor del sistema (software).",
    )

    ceos_edi_mode_invoice = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        string="EDI Factura",
        default="1",
        required=True,
    )
    ceos_edi_mode_credit_note = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        string="EDI Nota de crédito",
        default="1",
        required=True,
    )
    ceos_edi_mode_debit_note = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        string="EDI Nota de débito",
        default="1",
        required=True,
    )
    ceos_edi_mode_withhold = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        string="EDI Retención",
        default="1",
        required=True,
    )
    ceos_edi_mode_liquidation = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        string="EDI Liquidación de compra",
        default="1",
        required=True,
    )
    ceos_edi_mode_delivery_guide = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        string="EDI Guía de remisión",
        default="1",
        required=True,
    )
    ceos_edi_xml_export_path = fields.Char(
        string="Directorio export XML SRI",
        help="Ruta absoluta donde Odoo escribe el XML (Opción 2). "
        "Debe ser escribible por el usuario del servicio Odoo.",
    )
    ceos_purchase_api_post_mode = fields.Selection(
        [
            ("draft", "Dejar en borrador para revisión"),
            ("posted", "Publicar automáticamente (asiento contable)"),
        ],
        string="API compras: modo factura",
        default="draft",
        required=True,
        help="Comportamiento de POST /api/compras al crear facturas de proveedor desde XML SRI.",
    )

    def ceos_get_edi_mode_for_move(self, move):
        """Resolve EDI mode (1/2/3) for an account.move."""
        self.ensure_one()
        is_wth = getattr(move, "_l10n_ec_is_withholding", None)
        if callable(is_wth) and is_wth():
            return self.ceos_edi_mode_withhold or "1"
        internal = move.l10n_latam_document_type_id.internal_type if move.l10n_latam_document_type_id else ""
        if internal == "credit_note":
            return self.ceos_edi_mode_credit_note or "1"
        if internal == "debit_note":
            return self.ceos_edi_mode_debit_note or "1"
        if internal == "purchase_liquidation":
            return self.ceos_edi_mode_liquidation or "1"
        return self.ceos_edi_mode_invoice or "1"

    def ceos_get_edi_mode_delivery_guide(self):
        self.ensure_one()
        return self.ceos_edi_mode_delivery_guide or "1"

    def ceos_get_edi_ruc_proveedor_additional(self):
        """Dict fragment for XML infoAdicional (empty if RUC not configured)."""
        self.ensure_one()
        ruc = (self.ceos_edi_additional_ruc_proveedor or CEOS_EDI_RUC_PROVEEDOR or "").strip()
        if not ruc:
            return {}
        return {CEOS_EDI_RUC_PROVEEDOR_LABEL: ruc}
