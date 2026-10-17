# -*- coding: utf-8 -*-
import logging
import os
from lxml import etree
from markupsafe import Markup

from odoo import _, models
from odoo.tools import html_escape
from odoo.tools.xml_utils import cleanup_xml_node

_logger = logging.getLogger(__name__)


class AccountEdiFormat(models.Model):
    _inherit = "account.edi.format"

    def _l10n_ec_get_xml_common_values(self, move):
        vals = super()._l10n_ec_get_xml_common_values(move)
        vals["ceos_propina"] = 0.0
        return vals

    def _ceos_generate_xml_for_export(self, move):
        """
        Build invoice/withhold XML for Opción 2.
        Prefer unsigned XML (external emitter signs). If company wants signed and
        certificate works, still ok to export unsigned for Opción 2.
        """
        move_info = self._l10n_ec_get_xml_common_values(move)
        if move.journal_id.l10n_ec_withhold_type:
            template = "l10n_ec_edi.withhold_template"
            move_info.update(move._l10n_ec_get_withhold_edi_data())
        else:
            doc_type = move.l10n_latam_document_type_id.internal_type
            template = {
                "credit_note": "l10n_ec_edi.credit_note_template",
                "debit_note": "l10n_ec_edi.debit_note_template",
                "invoice": "l10n_ec_edi.invoice_template",
                "purchase_liquidation": "l10n_ec_edi.purchase_liquidation_template",
            }.get(doc_type)
            if not template:
                return None, [_("Tipo de documento no soportado para export XML: %s") % doc_type]
            move_info.update(move._l10n_ec_get_invoice_edi_data())

        errors = []
        if move_info.get("taxes_data"):
            errors += self._l10n_ec_remove_negative_lines_from_move_info(move_info)

        xml_content = self.env["ir.qweb"]._render(template, move_info)
        xml_content = cleanup_xml_node(xml_content)
        xml_string = etree.tostring(
            xml_content,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )
        return xml_string, errors

    def _ceos_export_xml_to_directory(self, move, xml_string):
        """Write XML to company export path. Returns (errors, attachment)."""
        company = move.company_id
        path = (company.ceos_edi_xml_export_path or "").strip()
        if not path:
            return [_("CEOS: configure el directorio de export XML SRI (Opción 2).")], None

        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            return [_("CEOS: no se pudo crear el directorio XML %s: %s") % (path, exc)], None

        auth = move.l10n_ec_authorization_number or move.name or str(move.id)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in auth)
        filename = "%s.xml" % safe_name
        full_path = os.path.join(path, filename)

        raw = xml_string if isinstance(xml_string, bytes) else xml_string.encode("utf-8")
        try:
            with open(full_path, "wb") as fh:
                fh.write(raw)
        except OSError as exc:
            return [_("CEOS: no se pudo escribir XML en %s: %s") % (full_path, exc)], None

        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "raw": raw,
                "res_model": move._name,
                "res_id": move.id,
                "mimetype": "application/xml",
            }
        )
        move.message_post(
            body=Markup(
                _("CEOS EDI Opción 2: XML (sin firmar) exportado a <code>%s</code>")
                % html_escape(full_path)
            ),
            attachment_ids=attachment.ids,
        )
        _logger.info("CEOS EDI mode 2: exported XML for move %s to %s", move.id, full_path)
        return [], attachment

    def _l10n_ec_post_move_edi(self, moves):
        """Respect CEOS EDI mode per document type (1=SRI, 2=dir, 3=skip)."""
        res = {}
        mode1_moves = self.env["account.move"]
        for move in moves:
            mode = move.company_id.ceos_get_edi_mode_for_move(move)
            if mode == "3":
                move.message_post(
                    body=_("CEOS EDI Opción 3: sin XML/SRI; solo contabilidad interna.")
                )
                res[move] = {
                    "success": True,
                    "error": "",
                    "attachment": None,
                    "blocking_level": False,
                }
                continue

            if mode == "2":
                # No firmar en Odoo: el emisor externo firma/autoriza.
                xml_string, errors = self._ceos_generate_xml_for_export(move)
                if errors and not xml_string:
                    res[move] = {
                        "success": False,
                        "error": "<br/>".join([html_escape(e) for e in errors]),
                        "attachment": None,
                        "blocking_level": "error",
                    }
                    continue
                exp_errors, attachment = self._ceos_export_xml_to_directory(move, xml_string)
                all_errors = list(errors or []) + list(exp_errors or [])
                res[move] = {
                    "success": not exp_errors,
                    "error": "<br/>".join([html_escape(e) for e in all_errors]),
                    "attachment": attachment,
                    "blocking_level": "error" if exp_errors else ("warning" if errors else False),
                }
                continue

            mode1_moves |= move

        if mode1_moves:
            res.update(super()._l10n_ec_post_move_edi(mode1_moves))
        return res
