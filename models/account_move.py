# -*- coding: utf-8 -*-
from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _ceos_edi_merge_ruc_proveedor(self, additional_info):
        """Add SRI campoAdicional RUC Proveedor (factura, NC, ND, liq., retención)."""
        self.ensure_one()
        info = dict(additional_info or {})
        info.update(self.company_id.ceos_get_edi_ruc_proveedor_additional())
        return info

    def _l10n_ec_get_invoice_additional_info(self):
        # Factura, nota crédito, nota débito, liquidación de compra (+ RIDE).
        return self._ceos_edi_merge_ruc_proveedor(super()._l10n_ec_get_invoice_additional_info())

    def _l10n_ec_get_withhold_additional_info(self):
        return self._ceos_edi_merge_ruc_proveedor(super()._l10n_ec_get_withhold_additional_info())

    def _l10n_ec_get_invoice_edi_data(self):
        data = super()._l10n_ec_get_invoice_edi_data()
        internal_type = self.l10n_latam_document_type_id.internal_type
        if internal_type not in ("invoice", "purchase_liquidation"):
            return data

        taxes_data = data.get("taxes_data") or {}
        tax_details = taxes_data.get("tax_details") or {}
        if tax_details:
            return data

        # Sin impuestos, el XSD del SRI exige totalConImpuestos antes de importeTotal.
        base_amount = abs(self.amount_untaxed_signed or self.amount_untaxed or 0.0)
        tax_details["vat0_ceos_fix"] = {
            "code": "2",
            "code_percentage": "0",
            "base_amount": base_amount,
            "rate": 0.0,
            "tax_amount": 0.0,
        }
        taxes_data["tax_details"] = tax_details
        data["taxes_data"] = taxes_data
        return data

    def _l10n_ec_check_number_prefix(self):
        """Vendor bills use the supplier's estab-ptoEmi (any prefix). Do not force journal emission."""
        vendor_bills = self.filtered(
            lambda m: m.move_type in ("in_invoice", "in_refund")
            and not getattr(m.journal_id, "l10n_ec_is_purchase_liquidation", False)
        )
        others = self - vendor_bills
        if others:
            return super(AccountMove, others)._l10n_ec_check_number_prefix()
        return None

    def _post(self, soft=True):
        moves = super()._post(soft)
        # Opción 3: no dejar clave de acceso Odoo (el emisor externo genera la suya).
        for move in moves.filtered(lambda m: m.country_code == "EC"):
            if move.company_id.ceos_get_edi_mode_for_move(move) == "3":
                move.l10n_ec_authorization_number = False
        return moves
