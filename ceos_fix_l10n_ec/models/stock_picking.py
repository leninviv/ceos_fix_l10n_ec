# -*- coding: utf-8 -*-
import logging
import os
from base64 import b64encode

from lxml import etree
from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _l10n_ec_get_delivery_guide_additional_info(self):
        """Guía de remisión: infoAdicional + RUC Proveedor (SRI)."""
        self.ensure_one()
        if "l10n_ec_edi_status" not in self._fields:
            info = {}
        else:
            info = dict(super()._l10n_ec_get_delivery_guide_additional_info() or {})
        info.update(self.company_id.ceos_get_edi_ruc_proveedor_additional())
        # SRI: no enviar campos vacíos
        return {k: v for k, v in info.items() if v not in (None, False, "")}

    def _l10n_ec_edi_get_delivery_guide_values(self):
        """Incluir additional_info en el XML (el template estándar solo usa note/origin)."""
        values = super()._l10n_ec_edi_get_delivery_guide_values()
        additional = dict(self._l10n_ec_get_delivery_guide_additional_info() or {})
        if values.get("origin"):
            additional.setdefault("Pedido", values["origin"])
        if values.get("note"):
            additional.setdefault("Novedades", values["note"])
        values["additional_info"] = {
            k: v for k, v in additional.items() if v not in (None, False, "")
        }
        return values

    def _l10n_ec_edi_create_delivery_guide(self):
        """
        Tras renderizar el XML, asegurar infoAdicional completa (RUC Proveedor, etc.).
        El template nativo de Odoo solo emite Pedido/Novedades; el inherit QWeb a veces
        no aplica hasta actualizar el módulo — este post-proceso lo garantiza.
        """
        xml_content = super()._l10n_ec_edi_create_delivery_guide()
        return self._ceos_inject_delivery_guide_info_adicional(xml_content)

    def _ceos_inject_delivery_guide_info_adicional(self, xml_content):
        """Merge additional_info into <infoAdicional> of the guiaRemision XML."""
        self.ensure_one()
        additional = dict(self._l10n_ec_get_delivery_guide_additional_info() or {})
        if self.origin:
            additional.setdefault("Pedido", self.origin)
        if self.note:
            note_txt = self.note.striptags().replace("\n", " ").strip()[:300]
            if note_txt:
                additional.setdefault("Novedades", note_txt)
        additional = {k: str(v).strip() for k, v in additional.items() if v not in (None, False, "")}
        if not additional:
            return xml_content

        if isinstance(xml_content, (bytes, str)):
            root = etree.fromstring(xml_content if isinstance(xml_content, bytes) else xml_content.encode("utf-8"))
        else:
            root = xml_content

        # Existing campos (nombre -> element)
        info_nodes = root.xpath(".//*[local-name()='infoAdicional']")
        if info_nodes:
            info_el = info_nodes[0]
        else:
            info_el = etree.SubElement(root, "infoAdicional")

        existing = {}
        for campo in list(info_el):
            if campo.tag is etree.Comment or not hasattr(campo, "get"):
                continue
            nombre = campo.get("nombre") or ""
            if nombre:
                existing[nombre] = campo

        for nombre, valor in additional.items():
            if nombre in existing:
                existing[nombre].text = valor
            else:
                el = etree.SubElement(info_el, "campoAdicional")
                el.set("nombre", nombre)
                el.text = valor

        return root

    def l10n_ec_action_create_delivery_guide(self):
        """
        Honor CEOS EDI mode for delivery guides.

        Opción 1: flujo Odoo (firmar + enviar SRI).
        Opción 2: XML sin firmar a directorio (sync externo); no SRI/demo.
        Opción 3: solo inventario.
        """
        if "l10n_ec_edi_status" not in self._fields:
            return True

        mode3 = self.filtered(
            lambda p: p.company_id.ceos_get_edi_mode_delivery_guide() == "3"
        )
        for picking in mode3:
            picking.message_post(
                body=_("CEOS EDI Opción 3 (guía): sin XML/SRI; solo operación de inventario.")
            )
            picking.write(
                {
                    "l10n_ec_edi_status": False,
                    "l10n_ec_delivery_guide_error": False,
                }
            )

        rest = self - mode3
        if not rest:
            return True

        mode2 = rest.filtered(
            lambda p: p.company_id.ceos_get_edi_mode_delivery_guide() == "2"
        )
        for picking in mode2:
            picking._ceos_create_and_export_delivery_guide()

        mode1 = rest - mode2
        if mode1:
            return super(StockPicking, mode1).l10n_ec_action_create_delivery_guide()
        return True

    def _ceos_create_and_export_delivery_guide(self):
        """Generate unsigned guía XML and write it to the Opción 2 export folder."""
        self.ensure_one()
        self._check_company()
        self._l10n_ec_edi_validations()

        path = (self.company_id.ceos_edi_xml_export_path or "").strip()
        if not path:
            raise UserError(
                _("CEOS: configure el directorio de export XML SRI (Opción 2) para guías.")
            )

        wh_id = self.picking_type_id.warehouse_id
        if not self.l10n_ec_edi_document_number:
            if not wh_id.l10n_ec_delivery_number_sequence_id:
                raise UserError(
                    _("El almacén %s no tiene secuencia de guía de remisión.")
                    % wh_id.display_name
                )
            self.l10n_ec_edi_document_number = (
                f"{wh_id.l10n_ec_entity}-{wh_id.l10n_ec_emission}-"
                f"{wh_id.l10n_ec_delivery_number_sequence_id.next_by_id()}"
            )
        self.l10n_ec_authorization_number = self._l10n_ec_get_authorization_number()

        xml_content = self._l10n_ec_edi_create_delivery_guide()
        if isinstance(xml_content, bytes):
            raw = xml_content
        elif isinstance(xml_content, str):
            raw = xml_content.encode("utf-8")
        else:
            # etree element from cleanup_xml_node
            raw = etree.tostring(
                xml_content,
                encoding="UTF-8",
                xml_declaration=True,
                pretty_print=True,
            )

        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise UserError(
                _("CEOS: no se pudo crear el directorio XML %s: %s") % (path, exc)
            ) from exc

        auth = self.l10n_ec_authorization_number or self.name or str(self.id)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in auth)
        # Misma convención que facturas Opción 2: <claveAcceso>.xml (sin prefijo guia_)
        filename = "%s.xml" % safe_name
        full_path = os.path.join(path, filename)

        try:
            with open(full_path, "wb") as fh:
                fh.write(raw)
        except OSError as exc:
            raise UserError(
                _("CEOS: no se pudo escribir XML en %s: %s") % (full_path, exc)
            ) from exc

        # 'sent' = procesado por CEOS (no dejar to_send: el cron Odoo mandaría DEMO/SRI)
        self.write(
            {
                "l10n_ec_edi_status": "sent",
                "l10n_ec_edi_content": b64encode(raw),
                "l10n_ec_delivery_guide_error": False,
            }
        )
        self.message_post(
            body=Markup(
                _("CEOS EDI Opción 2 (guía): XML (sin firmar) exportado a <code>%s</code>")
                % html_escape(full_path)
            )
        )
        _logger.info("CEOS EDI mode 2: guide %s -> %s", self.id, full_path)

    def l10n_ec_send_delivery_guide_to_send(self):
        """Never send Opción 2/3 guides to SRI (cron or 'Process now')."""
        if "l10n_ec_edi_status" not in self._fields:
            return True

        to_skip = self.filtered(
            lambda p: p.company_id.ceos_get_edi_mode_delivery_guide() in ("2", "3")
        )
        for picking in to_skip.filtered(lambda p: p.l10n_ec_edi_status == "to_send"):
            # Documento viejo quedado en to_send con Opción 2: exportar si se puede
            if picking.company_id.ceos_get_edi_mode_delivery_guide() == "2":
                try:
                    picking._ceos_create_and_export_delivery_guide()
                except UserError as err:
                    picking.l10n_ec_delivery_guide_error = str(err)
                    picking.message_post(body=str(err))
            else:
                picking.l10n_ec_edi_status = False

        rest = self - to_skip
        if rest:
            return super(StockPicking, rest).l10n_ec_send_delivery_guide_to_send()
        return True

    def _l10n_ec_cron_send_delivery_guide_to_sri(self):
        """Cron: Opción 1 → SRI; Opción 2 atascada en to_send → export; Opción 3 → ignorar."""
        if "l10n_ec_edi_status" not in self._fields:
            return
        from time import sleep

        pending = self.search([("l10n_ec_edi_status", "in", ("to_send", "to_cancel"))])
        for record in pending:
            mode = record.company_id.ceos_get_edi_mode_delivery_guide()
            if mode == "3":
                continue
            if mode == "2":
                if record.l10n_ec_edi_status == "to_send":
                    try:
                        record.with_context(
                            cron_skip_connection_errs=True
                        )._ceos_create_and_export_delivery_guide()
                    except UserError as err:
                        record.l10n_ec_delivery_guide_error = str(err)
                self.env.cr.commit()
                sleep(1)
                continue
            # Opción 1: flujo Odoo
            if record.l10n_ec_edi_status == "to_cancel":
                record.with_context(
                    cron_skip_connection_errs=True
                ).l10n_ec_send_delivery_guide_to_cancel()
            else:
                record.with_context(
                    cron_skip_connection_errs=True
                ).l10n_ec_send_delivery_guide_to_send()
            self.env.cr.commit()
            sleep(1)
