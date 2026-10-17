# -*- coding: utf-8 -*-
from odoo import api, models


class L10nEcWizardAccountWithhold(models.TransientModel):
    _inherit = "l10n_ec.wizard.account.withhold"

    @api.onchange("journal_id")
    def _onchange_ceos_suggest_sale_withhold_number(self):
        """Prefill retención venta con la sugerencia CEOS Secuencias."""
        for wizard in self:
            if (
                wizard.journal_id
                and wizard.journal_id.l10n_ec_withhold_type == "out_withhold"
                and wizard.manual_document_number
                and not wizard.document_number
            ):
                suggested = wizard.company_id.ceos_next_withhold_sale
                if suggested:
                    wizard.document_number = suggested
