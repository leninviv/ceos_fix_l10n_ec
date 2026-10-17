# -*- coding: utf-8 -*-
import logging
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DOC_NUMBER_RE = re.compile(r"^(\d{3})-(\d{3})-(\d{9})$")
NAME_SEQ_RE = re.compile(r"(\d{3})-(\d{3})-(\d{9})\s*$")

# internal_type -> (xmlid doc type, move_type)
SEED_SPECS = {
    "invoice": ("l10n_ec.ec_dt_01", "out_invoice"),
    "credit_note": ("l10n_ec.ec_dt_04", "out_refund"),
    "debit_note": ("l10n_ec.ec_dt_05", "out_invoice"),
    "purchase_liquidation": ("l10n_ec.ec_dt_03", "in_invoice"),
    "withhold": ("l10n_ec.ec_dt_07", "entry"),
}


class AccountMove(models.Model):
    _inherit = "account.move"

    def _ceos_parse_document_number(self, number_str):
        """Return (entity, emission, int_seq) from '001-002-000000075'."""
        value = (number_str or "").strip()
        match = DOC_NUMBER_RE.match(value)
        if not match:
            raise UserError(
                _("Formato inválido: %(number)s. Use 001-002-000000075.")
                % {"number": number_str}
            )
        entity, emission, seq = match.groups()
        return entity, emission, int(seq)

    def _ceos_format_move_name(self, doc_type, entity, emission, seq):
        return "%s %s-%s-%09d" % (doc_type.doc_code_prefix, entity, emission, seq)

    def _ceos_document_types(self, internal_type):
        return self.env["l10n_latam.document.type"].search(
            [
                ("internal_type", "=", internal_type),
                ("country_id.code", "=", "EC"),
            ]
        )

    def _ceos_seq_from_name(self, name):
        """Extract Ecuador sequential (9 digits) from move name."""
        if not name or name == "/":
            return 0
        match = NAME_SEQ_RE.search(name)
        return int(match.group(3)) if match else 0

    def _ceos_get_max_sequence(self, journal, internal_type, doc_type_ids=None):
        """
        Highest Ecuador sequential for journal + document internal_type.

        Uses the number embedded in `name` (…-XXXXXXXXX), not only
        sequence_number, because that column can disagree after ORM flush.
        """
        if not journal:
            return 0

        # Retenciones de compra: el nombre es "Ret 001-001-#########" y a veces
        # no traen l10n_latam_document_type_id; buscar por prefijo Ret.
        if internal_type == "withhold":
            self.env.cr.execute(
                """
                SELECT name
                  FROM account_move
                 WHERE journal_id = %s
                   AND name IS NOT NULL
                   AND name != '/'
                   AND name LIKE 'Ret %%'
                """,
                (journal.id,),
            )
            max_seq = 0
            for (name,) in self.env.cr.fetchall():
                max_seq = max(max_seq, self._ceos_seq_from_name(name))
            return max_seq

        if doc_type_ids is None:
            doc_types = self._ceos_document_types(internal_type)
            doc_type_ids = doc_types.ids
        if not doc_type_ids:
            return 0
        # Pure SQL — avoid ORM flush that could overwrite a just-written seed.
        self.env.cr.execute(
            """
            SELECT name
              FROM account_move
             WHERE journal_id = %s
               AND name IS NOT NULL
               AND name != '/'
               AND l10n_latam_document_type_id IN %s
            """,
            (journal.id, tuple(doc_type_ids)),
        )
        max_seq = 0
        for (name,) in self.env.cr.fetchall():
            max_seq = max(max_seq, self._ceos_seq_from_name(name))
        return max_seq

    def _ceos_get_max_withhold_sale_sequence(self, journal):
        """Retención en ventas: el número vive en `ref` (siempre manual)."""
        if not journal:
            return 0
        self.env.cr.execute(
            """
            SELECT ref
              FROM account_move
             WHERE journal_id = %s
               AND ref IS NOT NULL
               AND ref LIKE 'Ret %%'
            """,
            (journal.id,),
        )
        max_seq = 0
        for (ref,) in self.env.cr.fetchall():
            max_seq = max(max_seq, self._ceos_seq_from_name(ref))
        # También considerar sugerencia guardada en compañía
        suggested = journal.company_id.ceos_next_withhold_sale or ""
        if suggested.strip():
            try:
                _e, _m, seq = self._ceos_parse_document_number(suggested.strip())
                # La sugerencia es el *siguiente*; el max implícito es seq-1
                max_seq = max(max_seq, seq - 1)
            except UserError:
                pass
        return max_seq

    def _ceos_get_next_withhold_sale_number(self, journal):
        if not journal or not journal.l10n_ec_entity or not journal.l10n_ec_emission:
            return False
        # Preferir valor guardado en compañía si es >= max+1
        company = journal.company_id
        nxt = self._ceos_get_max_withhold_sale_sequence(journal) + 1
        auto = "%s-%s-%09d" % (journal.l10n_ec_entity, journal.l10n_ec_emission, nxt)
        if company.ceos_next_withhold_sale:
            try:
                _e, _m, seq = self._ceos_parse_document_number(
                    company.ceos_next_withhold_sale.strip()
                )
                if seq >= nxt:
                    return company.ceos_next_withhold_sale.strip()
            except UserError:
                pass
        return auto

    def _ceos_set_next_withhold_sale(self, journal, next_number_str):
        """Guarda sugerencia de retención venta (Odoo no auto-numera ventas)."""
        if not journal:
            raise UserError(_("Seleccione un diario de retención de ventas."))
        entity, emission, desired_next = self._ceos_parse_document_number(next_number_str)
        if entity != journal.l10n_ec_entity or emission != journal.l10n_ec_emission:
            raise UserError(
                _("El prefijo no coincide con el diario de retención de ventas.")
            )
        current_max = self._ceos_get_max_withhold_sale_sequence(journal)
        if desired_next <= current_max:
            raise UserError(
                _(
                    "No se puede fijar retención venta en %(desired)s: "
                    "ya hay un secuencial %(max)s (en ref o sugerencia)."
                )
                % {
                    "desired": "%09d" % desired_next,
                    "max": "%09d" % current_max,
                }
            )
        journal.company_id.sudo().write(
            {"ceos_next_withhold_sale": next_number_str.strip()}
        )
        return desired_next

    def _ceos_get_next_document_number(self, journal, internal_type):
        if not journal or not journal.l10n_ec_entity or not journal.l10n_ec_emission:
            return False
        nxt = self._ceos_get_max_sequence(journal, internal_type) + 1
        return "%s-%s-%09d" % (journal.l10n_ec_entity, journal.l10n_ec_emission, nxt)

    def _ceos_seed_ref(self, internal_type, journal):
        return "CEOS_SEQ_SEED:%s:%s" % (internal_type, journal.id)

    def _ceos_get_or_create_seed_move(self, journal, internal_type, doc_type):
        spec = SEED_SPECS.get(internal_type)
        if not spec:
            raise UserError(_("Tipo interno no soportado: %s") % internal_type)
        _xmlid, move_type = spec
        ref = self._ceos_seed_ref(internal_type, journal)
        Move = self.env["account.move"].sudo()
        seed = Move.search(
            [
                ("journal_id", "=", journal.id),
                ("ref", "=", ref),
            ],
            limit=1,
        )
        if seed:
            return seed

        partner = journal.company_id.partner_id
        # Crear en borrador y pasar a cancelado por SQL (evita side-effects EDI).
        seed = Move.with_context(
            check_move_validity=False,
            skip_invoice_sync=True,
            tracking_disable=True,
            mail_create_nolog=True,
            mail_notrack=True,
        ).create(
            {
                "move_type": move_type,
                "journal_id": journal.id,
                "company_id": journal.company_id.id,
                "partner_id": partner.id,
                "date": fields.Date.context_today(self),
                "invoice_date": fields.Date.context_today(self),
                "l10n_latam_document_type_id": doc_type.id,
                "ref": ref,
                "narration": _(
                    "Ancla de secuencia CEOS (%s). No publicar ni eliminar."
                )
                % internal_type,
            }
        )
        self.env.cr.execute(
            """
            UPDATE account_move
               SET state = 'cancel',
                   auto_post = 'no'
             WHERE id = %s
            """,
            (seed.id,),
        )
        seed.invalidate_recordset(["state", "auto_post"])
        return seed

    def _ceos_write_seed_name(self, seed, seed_name, prefix, seed_seq, doc_type, journal, move_type):
        """Persist seed name/sequence via SQL and verify the row."""
        self.env.cr.execute(
            """
            UPDATE account_move
               SET name = %s,
                   sequence_prefix = %s,
                   sequence_number = %s,
                   l10n_latam_document_type_id = %s,
                   journal_id = %s,
                   move_type = %s,
                   state = 'cancel'
             WHERE id = %s
            """,
            (
                seed_name,
                prefix,
                seed_seq,
                doc_type.id,
                journal.id,
                move_type,
                seed.id,
            ),
        )
        # Drop ORM cache so later reads cannot resurrect old name='/'
        seed.invalidate_recordset()
        self.env.cr.execute(
            """
            SELECT name, sequence_number, l10n_latam_document_type_id, state
              FROM account_move
             WHERE id = %s
            """,
            (seed.id,),
        )
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("No se encontró el asiento ancla de secuencia."))
        name, seq_num, doc_id, state = row
        parsed = self._ceos_seq_from_name(name)
        if name != seed_name or parsed != seed_seq or doc_id != doc_type.id:
            raise UserError(
                _(
                    "No se pudo grabar el ancla de secuencia.\n"
                    "name=%(name)s seq=%(seq)s doc=%(doc)s state=%(state)s\n"
                    "esperado name=%(exp_name)s seq=%(exp_seq)s"
                )
                % {
                    "name": name,
                    "seq": seq_num,
                    "doc": doc_id,
                    "state": state,
                    "exp_name": seed_name,
                    "exp_seq": seed_seq,
                }
            )
        # Alinear sequence_number con el número Ecuador del nombre
        if seq_num != seed_seq:
            self.env.cr.execute(
                """
                UPDATE account_move
                   SET sequence_number = %s,
                       sequence_prefix = %s
                 WHERE id = %s
                """,
                (seed_seq, prefix, seed.id),
            )

    def _ceos_seed_next_sequence(self, journal, internal_type, next_number_str):
        """
        Hace que el próximo documento use next_number_str.

        Mecanismo: asiento cancelado ancla con número N-1 (mismo patrón
        que el ajuste manual en PostgreSQL). Permite saltar huecos cuando
        ya se usaron números en otro sistema.
        """
        if not journal:
            raise UserError(_("Seleccione un diario."))
        if not journal.l10n_ec_entity or not journal.l10n_ec_emission:
            raise UserError(
                _("El diario %s no tiene establecimiento / punto de emisión.")
                % journal.display_name
            )

        entity, emission, desired_next = self._ceos_parse_document_number(next_number_str)
        if entity != journal.l10n_ec_entity or emission != journal.l10n_ec_emission:
            raise UserError(
                _(
                    "El prefijo %(got)s no coincide con el diario "
                    "%(entity)s-%(emission)s."
                )
                % {
                    "got": "%s-%s" % (entity, emission),
                    "entity": journal.l10n_ec_entity,
                    "emission": journal.l10n_ec_emission,
                }
            )
        if desired_next < 1:
            raise UserError(_("El secuencial debe ser al menos 000000001."))

        spec = SEED_SPECS[internal_type]
        doc_type = self.env.ref(spec[0])
        move_type = spec[1]
        doc_type_ids = self._ceos_document_types(internal_type).ids
        if not doc_type_ids:
            raise UserError(
                _("No hay tipos de documento Ecuador para %s.") % internal_type
            )

        current_max = self._ceos_get_max_sequence(
            journal, internal_type, doc_type_ids=doc_type_ids
        )
        current_next = current_max + 1
        if desired_next == current_next:
            return current_next

        # No bajar por debajo de documentos reales ya emitidos.
        if desired_next <= current_max:
            raise UserError(
                _(
                    "No se puede fijar el siguiente en %(desired)s: "
                    "ya existe un documento con secuencial %(max)s. "
                    "Solo se permite avanzar (siguiente actual: %(next)s)."
                )
                % {
                    "desired": "%09d" % desired_next,
                    "max": "%09d" % current_max,
                    "next": "%09d" % current_next,
                }
            )

        seed_seq = desired_next - 1
        seed_name = self._ceos_format_move_name(doc_type, entity, emission, seed_seq)
        prefix = "%s %s-%s-" % (doc_type.doc_code_prefix, entity, emission)

        # Colisión con documento real (no ancla CEOS)
        self.env.cr.execute(
            """
            SELECT id, name, ref
              FROM account_move
             WHERE journal_id = %s
               AND name = %s
               AND (ref IS NULL OR ref NOT LIKE 'CEOS_SEQ_SEED:%%')
             LIMIT 1
            """,
            (journal.id, seed_name),
        )
        conflict = self.env.cr.fetchone()
        if conflict:
            raise UserError(
                _(
                    "Ya existe el documento %(name)s. "
                    "Elija un siguiente mayor a ese número."
                )
                % {"name": seed_name}
            )

        seed = self._ceos_get_or_create_seed_move(journal, internal_type, doc_type)
        self._ceos_write_seed_name(
            seed, seed_name, prefix, seed_seq, doc_type, journal, move_type
        )

        new_max = self._ceos_get_max_sequence(
            journal, internal_type, doc_type_ids=doc_type_ids
        )
        new_next = new_max + 1
        if new_next != desired_next:
            raise UserError(
                _(
                    "No se pudo fijar la secuencia.\n"
                    "Siguiente calculado=%(got)s, esperado=%(exp)s.\n"
                    "Ancla id=%(seed)s name=%(name)s.\n"
                    "Si el problema continúa, revise asientos cancelados "
                    "con referencia CEOS_SEQ_SEED."
                )
                % {
                    "got": new_next,
                    "exp": desired_next,
                    "seed": seed.id,
                    "name": seed_name,
                }
            )

        _logger.info(
            "CEOS secuencia: user=%s journal=%s type=%s next=%s seed_move=%s",
            self.env.user.login,
            journal.display_name,
            internal_type,
            next_number_str,
            seed.id,
        )
        return new_next
