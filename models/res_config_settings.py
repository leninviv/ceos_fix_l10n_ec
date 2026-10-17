# -*- coding: utf-8 -*-
from odoo import fields, models

from .res_company import CEOS_EDI_MODE_SELECTION


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ceos_edi_mode_invoice = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        related="company_id.ceos_edi_mode_invoice",
        readonly=False,
    )
    ceos_edi_mode_credit_note = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        related="company_id.ceos_edi_mode_credit_note",
        readonly=False,
    )
    ceos_edi_mode_debit_note = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        related="company_id.ceos_edi_mode_debit_note",
        readonly=False,
    )
    ceos_edi_mode_withhold = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        related="company_id.ceos_edi_mode_withhold",
        readonly=False,
    )
    ceos_edi_mode_liquidation = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        related="company_id.ceos_edi_mode_liquidation",
        readonly=False,
    )
    ceos_edi_mode_delivery_guide = fields.Selection(
        CEOS_EDI_MODE_SELECTION,
        related="company_id.ceos_edi_mode_delivery_guide",
        readonly=False,
    )
    ceos_edi_xml_export_path = fields.Char(
        related="company_id.ceos_edi_xml_export_path",
        readonly=False,
    )
    ceos_edi_additional_ruc_proveedor = fields.Char(
        related="company_id.ceos_edi_additional_ruc_proveedor",
        readonly=False,
    )
    ceos_purchase_api_post_mode = fields.Selection(
        related="company_id.ceos_purchase_api_post_mode",
        readonly=False,
    )
