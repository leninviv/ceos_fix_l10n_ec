# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CeosEdiSequenceWizard(models.TransientModel):
    _name = "ceos.edi.sequence.wizard"
    _description = "CEOS Secuencias documentos EDI"

    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
    )
    sale_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de ventas",
        domain="[('company_id', '=', company_id), ('type', '=', 'sale'), "
        "('l10n_ec_entity', '!=', False), ('l10n_ec_emission', '!=', False)]",
        required=True,
    )
    liquidation_journal_id = fields.Many2one(
        "account.journal",
        string="Diario liquidación de compra",
        domain="[('company_id', '=', company_id), "
        "('l10n_ec_is_purchase_liquidation', '=', True)]",
    )
    withhold_purchase_journal_id = fields.Many2one(
        "account.journal",
        string="Diario retención compra",
        domain="[('company_id', '=', company_id), "
        "('l10n_ec_withhold_type', '=', 'in_withhold')]",
    )
    withhold_sale_journal_id = fields.Many2one(
        "account.journal",
        string="Diario retención venta",
        domain="[('company_id', '=', company_id), "
        "('l10n_ec_withhold_type', '=', 'out_withhold')]",
    )

    establishment_label = fields.Char(
        string="Establecimiento ventas",
        compute="_compute_establishment_label",
    )
    has_delivery_guide = fields.Boolean(compute="_compute_delivery_guide")
    warehouse_label = fields.Char(
        string="Almacén guía",
        compute="_compute_delivery_guide",
    )

    current_next_invoice = fields.Char(
        string="Siguiente factura (actual)",
        compute="_compute_current_next",
    )
    current_next_credit_note = fields.Char(
        string="Siguiente NC (actual)",
        compute="_compute_current_next",
    )
    current_next_debit_note = fields.Char(
        string="Siguiente ND (actual)",
        compute="_compute_current_next",
    )
    current_next_purchase_liquidation = fields.Char(
        string="Siguiente liquidación (actual)",
        compute="_compute_current_next",
    )
    current_next_withhold_purchase = fields.Char(
        string="Siguiente retención compra (actual)",
        compute="_compute_current_next",
    )
    current_next_withhold_sale = fields.Char(
        string="Siguiente retención venta (actual)",
        compute="_compute_current_next",
    )
    current_next_delivery_guide = fields.Char(
        string="Siguiente guía remisión (actual)",
        compute="_compute_current_next",
    )

    next_invoice = fields.Char(
        string="Nueva siguiente factura",
        help="Formato 001-002-000000075.",
    )
    next_credit_note = fields.Char(string="Nueva siguiente nota de crédito")
    next_debit_note = fields.Char(string="Nueva siguiente nota de débito")
    next_purchase_liquidation = fields.Char(
        string="Nueva siguiente liquidación de compra"
    )
    next_withhold_purchase = fields.Char(
        string="Nueva siguiente retención compra"
    )
    next_withhold_sale = fields.Char(
        string="Nueva siguiente retención venta (sugerida)",
        help="Las retenciones de venta son manuales en Odoo; "
        "este valor se sugiere al crear la retención.",
    )
    next_delivery_guide = fields.Char(
        string="Nueva siguiente guía de remisión",
        help="Formato 001-001-000000001 (establecimiento del almacén).",
    )

    # -------------------------------------------------------------------------
    # Defaults / computes
    # -------------------------------------------------------------------------

    def _ceos_find_warehouse(self, company):
        if "stock.warehouse" not in self.env:
            return False
        WH = self.env["stock.warehouse"]
        if "l10n_ec_delivery_number" not in WH._fields:
            return False
        return WH.search(
            [
                ("company_id", "=", company.id),
                ("l10n_ec_entity", "!=", False),
                ("l10n_ec_emission", "!=", False),
                ("l10n_ec_delivery_number_sequence_id", "!=", False),
            ],
            order="id",
            limit=1,
        )

    def _ceos_delivery_next_str(self, warehouse):
        if not warehouse or not warehouse.l10n_ec_entity:
            return False
        seq = warehouse.l10n_ec_delivery_number_sequence_id
        number_next = seq.number_next if seq else warehouse.l10n_ec_delivery_number or 1
        return "%s-%s-%09d" % (
            warehouse.l10n_ec_entity,
            warehouse.l10n_ec_emission,
            int(number_next),
        )

    def _ceos_set_delivery_next(self, warehouse, next_number_str):
        Move = self.env["account.move"]
        entity, emission, desired = Move._ceos_parse_document_number(next_number_str)
        if (
            entity != warehouse.l10n_ec_entity
            or emission != warehouse.l10n_ec_emission
        ):
            raise UserError(
                _(
                    "El prefijo de la guía no coincide con el almacén "
                    "%(entity)s-%(emission)s."
                )
                % {
                    "entity": warehouse.l10n_ec_entity,
                    "emission": warehouse.l10n_ec_emission,
                }
            )
        if desired < 1:
            raise UserError(_("El secuencial de guía debe ser al menos 1."))
        seq = warehouse.l10n_ec_delivery_number_sequence_id
        if not seq:
            raise UserError(
                _("El almacén no tiene secuencia de guía de remisión configurada.")
            )
        current = int(seq.number_next or 1)
        if desired < current:
            raise UserError(
                _(
                    "No se puede bajar la guía de %(current)s a %(desired)s. "
                    "Solo avanzar."
                )
                % {"current": "%09d" % current, "desired": "%09d" % desired}
            )
        seq.sudo().number_next = desired
        return desired

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env.company
        Move = self.env["account.move"]
        Journal = self.env["account.journal"]

        sale = Journal.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "sale"),
                ("l10n_ec_entity", "!=", False),
                ("l10n_ec_emission", "!=", False),
            ],
            order="id",
            limit=1,
        )
        preferred = Journal.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "sale"),
                ("l10n_ec_entity", "=", "001"),
                ("l10n_ec_emission", "=", "002"),
            ],
            limit=1,
        )
        if preferred:
            sale = preferred

        liq = Journal.search(
            [
                ("company_id", "=", company.id),
                ("l10n_ec_is_purchase_liquidation", "=", True),
            ],
            limit=1,
        )
        wh_in = Journal.search(
            [
                ("company_id", "=", company.id),
                ("l10n_ec_withhold_type", "=", "in_withhold"),
            ],
            limit=1,
        )
        wh_out = Journal.search(
            [
                ("company_id", "=", company.id),
                ("l10n_ec_withhold_type", "=", "out_withhold"),
            ],
            limit=1,
        )

        res["company_id"] = company.id
        if sale:
            res["sale_journal_id"] = sale.id
            res["next_invoice"] = Move._ceos_get_next_document_number(sale, "invoice")
            res["next_credit_note"] = Move._ceos_get_next_document_number(
                sale, "credit_note"
            )
            res["next_debit_note"] = Move._ceos_get_next_document_number(
                sale, "debit_note"
            )
        if liq:
            res["liquidation_journal_id"] = liq.id
            res["next_purchase_liquidation"] = Move._ceos_get_next_document_number(
                liq, "purchase_liquidation"
            )
        if wh_in:
            res["withhold_purchase_journal_id"] = wh_in.id
            res["next_withhold_purchase"] = Move._ceos_get_next_document_number(
                wh_in, "withhold"
            )
        if wh_out:
            res["withhold_sale_journal_id"] = wh_out.id
            res["next_withhold_sale"] = Move._ceos_get_next_withhold_sale_number(wh_out)

        # Guía de remisión (si l10n_ec_edi_stock está instalado)
        if "stock.warehouse" in self.env:
            wh = self.env["stock.warehouse"].search(
                [
                    ("company_id", "=", company.id),
                    ("l10n_ec_entity", "!=", False),
                    ("l10n_ec_emission", "!=", False),
                    ("l10n_ec_delivery_number_sequence_id", "!=", False),
                ],
                order="id",
                limit=1,
            )
            if wh:
                number_next = (
                    wh.l10n_ec_delivery_number_sequence_id.number_next
                    if wh.l10n_ec_delivery_number_sequence_id
                    else 1
                )
                res["next_delivery_guide"] = "%s-%s-%09d" % (
                    wh.l10n_ec_entity,
                    wh.l10n_ec_emission,
                    int(number_next),
                )
        return res

    @api.depends("sale_journal_id")
    def _compute_establishment_label(self):
        for wiz in self:
            journal = wiz.sale_journal_id
            if journal and journal.l10n_ec_entity and journal.l10n_ec_emission:
                wiz.establishment_label = "%s-%s" % (
                    journal.l10n_ec_entity,
                    journal.l10n_ec_emission,
                )
            else:
                wiz.establishment_label = False

    @api.depends("company_id")
    def _compute_delivery_guide(self):
        for wiz in self:
            wh = wiz._ceos_find_warehouse(wiz.company_id)
            if wh:
                wiz.has_delivery_guide = True
                wiz.warehouse_label = "%s (%s-%s)" % (
                    wh.name,
                    wh.l10n_ec_entity,
                    wh.l10n_ec_emission,
                )
            else:
                wiz.has_delivery_guide = False
                wiz.warehouse_label = False

    @api.depends(
        "sale_journal_id",
        "liquidation_journal_id",
        "withhold_purchase_journal_id",
        "withhold_sale_journal_id",
        "company_id",
        "has_delivery_guide",
    )
    def _compute_current_next(self):
        Move = self.env["account.move"]
        for wiz in self:
            sale = wiz.sale_journal_id
            wiz.current_next_invoice = (
                Move._ceos_get_next_document_number(sale, "invoice") if sale else False
            )
            wiz.current_next_credit_note = (
                Move._ceos_get_next_document_number(sale, "credit_note") if sale else False
            )
            wiz.current_next_debit_note = (
                Move._ceos_get_next_document_number(sale, "debit_note") if sale else False
            )
            liq = wiz.liquidation_journal_id
            wiz.current_next_purchase_liquidation = (
                Move._ceos_get_next_document_number(liq, "purchase_liquidation")
                if liq
                else False
            )
            wh_in = wiz.withhold_purchase_journal_id
            wiz.current_next_withhold_purchase = (
                Move._ceos_get_next_document_number(wh_in, "withhold") if wh_in else False
            )
            wh_out = wiz.withhold_sale_journal_id
            wiz.current_next_withhold_sale = (
                Move._ceos_get_next_withhold_sale_number(wh_out) if wh_out else False
            )
            if wiz.has_delivery_guide:
                warehouse = wiz._ceos_find_warehouse(wiz.company_id)
                wiz.current_next_delivery_guide = wiz._ceos_delivery_next_str(warehouse)
            else:
                wiz.current_next_delivery_guide = False

    @api.onchange(
        "sale_journal_id",
        "liquidation_journal_id",
        "withhold_purchase_journal_id",
        "withhold_sale_journal_id",
        "company_id",
    )
    def _onchange_journals_prefill(self):
        self.next_invoice = self.current_next_invoice
        self.next_credit_note = self.current_next_credit_note
        self.next_debit_note = self.current_next_debit_note
        self.next_purchase_liquidation = self.current_next_purchase_liquidation
        self.next_withhold_purchase = self.current_next_withhold_purchase
        self.next_withhold_sale = self.current_next_withhold_sale
        self.next_delivery_guide = self.current_next_delivery_guide

    def action_apply(self):
        self.ensure_one()
        Move = self.env["account.move"]
        changes = []

        def _apply_move(journal, internal_type, value, label):
            if not value:
                return
            if not journal:
                raise UserError(
                    _("No hay diario para %(label)s.") % {"label": label}
                )
            before = Move._ceos_get_next_document_number(journal, internal_type)
            if (value or "").strip() == (before or "").strip():
                return
            Move._ceos_seed_next_sequence(journal, internal_type, value)
            after = Move._ceos_get_next_document_number(journal, internal_type)
            changes.append("%s: %s → %s" % (label, before, after))

        _apply_move(self.sale_journal_id, "invoice", self.next_invoice, _("Factura"))
        _apply_move(
            self.sale_journal_id,
            "credit_note",
            self.next_credit_note,
            _("Nota de crédito"),
        )
        _apply_move(
            self.sale_journal_id,
            "debit_note",
            self.next_debit_note,
            _("Nota de débito"),
        )
        if self.next_purchase_liquidation:
            _apply_move(
                self.liquidation_journal_id,
                "purchase_liquidation",
                self.next_purchase_liquidation,
                _("Liquidación de compra"),
            )
        if self.next_withhold_purchase:
            _apply_move(
                self.withhold_purchase_journal_id,
                "withhold",
                self.next_withhold_purchase,
                _("Retención compra"),
            )

        if self.next_withhold_sale:
            journal = self.withhold_sale_journal_id
            if not journal:
                raise UserError(_("No hay diario de retención de ventas."))
            before = Move._ceos_get_next_withhold_sale_number(journal)
            if (self.next_withhold_sale or "").strip() != (before or "").strip():
                Move._ceos_set_next_withhold_sale(journal, self.next_withhold_sale)
                after = Move._ceos_get_next_withhold_sale_number(journal)
                changes.append(
                    "%s: %s → %s" % (_("Retención venta"), before, after)
                )

        if self.next_delivery_guide and self.has_delivery_guide:
            warehouse = self._ceos_find_warehouse(self.company_id)
            before = self._ceos_delivery_next_str(warehouse)
            if (self.next_delivery_guide or "").strip() != (before or "").strip():
                self._ceos_set_delivery_next(warehouse, self.next_delivery_guide)
                after = self._ceos_delivery_next_str(warehouse)
                changes.append(
                    "%s: %s → %s" % (_("Guía de remisión"), before, after)
                )

        if not changes:
            raise UserError(_("Indique al menos un número siguiente a aplicar."))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Secuencias actualizadas"),
                "message": "\n".join(changes),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
