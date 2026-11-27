"""
Impresión de tickets en impresora térmica ESC/POS (Windows, sin dependencias).

Usa la API de impresión de Windows por ctypes y envía los bytes directamente
a la impresora (trabajo RAW). Si la impresora no acepta RAW, el POS muestra
el ticket en el navegador para imprimirlo con el controlador normal.
"""

import datetime
import os
import struct
import textwrap
import zlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKET_DIR = os.path.join(BASE_DIR, 'tickets')
LOGO_PATH = os.path.join(TICKET_DIR, 'logo.png')
_LOGO_CACHE = {}

METHOD_LABEL = {
    'efectivo': 'Efectivo',
    'tarjeta': 'Tarjeta',
    'transferencia': 'Transferencia',
}


def fmt_money(value):
    return f"${float(value or 0):,.2f}"


def _cents(value):
    """Redondea a centavos (mismo criterio que pos_core.money)."""
    return round(float(value or 0) + 1e-9, 2)


def fmt_qty(value):
    value = float(value or 0)
    return str(int(value)) if value == int(value) else f'{value:.2f}'.rstrip('0')


def fmt_qty_unit(value, unit=None):
    """Cantidad con su abreviatura: 0.75 kg, 2 pza, 750 gr."""
    text = fmt_qty(value)
    unit = (unit or '').strip()
    return f'{text} {unit}' if unit else text


def _wrap(text, width):
    lines = []
    for raw in str(text).splitlines() or ['']:
        if not raw.strip():
            lines.append('')
            continue
        lines.extend(textwrap.wrap(raw, width) or [''])
    return lines


def build_ticket(sale, config):
    """Ticket de venta en texto de ancho fijo (con total centrado)."""
    width = int(config.get('printer', {}).get('width', 42) or 42)
    return render_ticket_text(build_ticket_lines(sale, config), width)


def render_ticket_text(lines, width):
    """Convierte las líneas del ticket (tipo, texto) a texto plano."""
    out = []
    for kind, text in lines:
        if kind == 'logo':
            continue
        if kind in ('store_name', 'total_label', 'total_amount'):
            out.append(center_text(text, width))
        else:
            out.append(text)
    return '\n'.join(out)


def center_text(text, width):
    """Centra un texto en el ancho del ticket."""
    text = str(text)
    if len(text) >= width:
        return text
    left = (width - len(text)) // 2
    return ' ' * left + text


def build_ticket_lines(sale, config):
    """Ticket de venta como lista de (tipo, texto).

    Tipos:
      'text'         -> línea normal de ancho fijo
      'total_label'  -> palabra TOTAL (se centra y se imprime en negritas)
      'total_amount' -> importe total (se centra y se imprime a doble tamaño)
    """
    width = int(config.get('printer', {}).get('width', 42) or 42)
    store = config.get('store', {})
    name = (store.get('name') or 'LA COLONIAL').strip()
    rfc = (store.get('rfc') or '').strip()
    address = (store.get('address') or '').strip()
    phone = (store.get('phone') or '').strip()
    footer = (store.get('footer') or '¡Gracias por su compra!').strip()

    lines = []
    line = '=' * width
    thin = '-' * width
    if os.path.exists(LOGO_PATH):
        lines.append(('logo', ''))
    for chunk in _wrap(name.upper(), width):
        lines.append(('store_name', chunk))
    if rfc:
        lines.append(('text', f'RFC: {rfc}'.center(width)))
    if store.get('show_address', True):
        for chunk in _wrap(address, width) if address else []:
            lines.append(('text', chunk.center(width)))
        if phone:
            lines.append(('text', f'Tel: {phone}'.center(width)))
    lines.append(('text', line))
    lines.append(('text', f'Ticket: {sale["folio"]}'))
    created = sale.get('created_at') or ''
    try:
        dt = datetime.datetime.fromisoformat(created)
        created = dt.strftime('%d/%m/%Y %H:%M')
    except ValueError:
        pass
    lines.append(('text', f'Fecha : {created}'))
    user_name = (sale.get("user_name") or "").strip()
    user_role = (sale.get("user_role") or "").strip()
    paid_by_name = (sale.get("paid_by_name") or "").strip()
    is_pending = (sale.get('status') == 'pending')
    is_seller = (user_role == 'cajero')
    if is_pending:
        lines.append(('text', f'Vendedor: {user_name}'))
    elif is_seller and paid_by_name and paid_by_name != user_name:
        lines.append(('text', f'Vendedor: {user_name}'))
        lines.append(('text', f'Cobró   : {paid_by_name}'))
    else:
        lines.append(('text', f'Cajero  : {paid_by_name or user_name}'))
    if sale.get('customer_name'):
        for chunk in _wrap(f'Cliente : {sale["customer_name"]}', width):
            lines.append(('text', chunk))
        if sale.get('customer_rfc'):
            for chunk in _wrap(f'RFC     : {sale["customer_rfc"]}', width):
                lines.append(('text', chunk))
    lines.append(('text', thin))
    lines.append(('text', f'{"Cant":<9}{"Producto":<{width - 20}}{"Importe":>11}'))
    lines.append(('text', thin))

    name_width = width - 20
    for item in sale.get('items', []):
        chunks = _wrap(item['name'], name_width)
        first = chunks[0] if chunks else ''
        qty_label = fmt_qty_unit(item.get('qty'), item.get('unit'))
        lines.append(('text', f'{qty_label:<9}{first:<{name_width}}{fmt_money(item["line_total"]):>11}'))
        for extra in chunks[1:]:
            lines.append(('text', f'{"":<9}{extra}'))
        if float(item['qty']) != 1:
            lines.append(('text', f'{"":<9}{qty_label} x {fmt_money(item["unit_price"])}'))

    lines.append(('text', thin))
    counts = {}
    for item in sale.get('items', []):
        unit = (item.get('unit') or '').strip()
        counts[unit] = counts.get(unit, 0) + float(item.get('qty') or 0)
    parts = []
    for unit, value in counts.items():
        parts.append(f'{fmt_qty(value)} {unit}'.strip())
    for chunk in _wrap('Artículos: ' + ' · '.join(parts), width):
        lines.append(('text', chunk))

    # --- Bloque del total: palabra e importe destacados ---
    lines.append(('text', ''))
    lines.append(('text', ''))
    lines.append(('total_label', 'TOTAL'))
    lines.append(('total_amount', fmt_money(sale['total'])))
    lines.append(('text', line))

    for payment in sale.get('payments', []):
        label = METHOD_LABEL.get(payment['method'], payment['method'])
        if payment.get('reference'):
            label += f' {payment["reference"]}'
        lines.append(('text', f'{label:<{width - 11}}{fmt_money(payment["amount"]):>11}'))
    if float(sale.get('change_given') or 0):
        lines.append(('text', f'{"Cambio":<{width - 11}}{fmt_money(sale["change_given"]):>11}'))

    if is_pending:
        lines.append(('text', line))
        lines.append(('text', '*** FAVOR DE PAGAR EN CAJA ***'.center(width)))

    if sale.get('status') == 'cancelled':
        lines.append(('text', line))
        lines.append(('text', '*** VENTA CANCELADA ***'.center(width)))
        if sale.get('cancel_reason'):
            for chunk in _wrap('Motivo: ' + sale['cancel_reason'], width):
                lines.append(('text', chunk.center(width)))

    lines.append(('text', line))
    for chunk in _wrap(footer, width):
        lines.append(('text', chunk.center(width)))
    lines.append(('text', ''))
    lines.append(('text', ''))
    return lines


def build_shift_report(summary, config):
    """Corte de caja en texto de ancho fijo."""
    width = int(config.get('printer', {}).get('width', 42) or 42)
    store = config.get('store', {})
    line = '=' * width
    thin = '-' * width
    rows = [
        line,
        (store.get('name') or 'La Colonial').upper().center(width),
        'CORTE DE CAJA'.center(width),
        line,
        f'Turno   : #{summary["id"]}',
        f'Cajero  : {summary.get("user_name") or ""}',
        f'Abierto : {summary.get("opened_at") or ""}',
        f'Cerrado : {summary.get("closed_at") or ""}',
        thin,
        f'Tickets           {summary.get("sales_count", 0):>8}',
        f'Ventas totales    {fmt_money(summary.get("sales_total")):>8}',
        f'Cancelaciones     {summary.get("cancelled_count", 0):>8}',
        f'Devoluciones      {summary.get("returns_count", 0):>8}',
        f'Por cobrar        {summary.get("pending_count", 0):>8}',
        thin,
        f'Efectivo          {fmt_money(summary.get("by_method", {}).get("efectivo", 0)):>8}',
        f'Tarjeta           {fmt_money(summary.get("by_method", {}).get("tarjeta", 0)):>8}',
        f'Transferencia     {fmt_money(summary.get("by_method", {}).get("transferencia", 0)):>8}',
        thin,
        f'Fondo inicial     {fmt_money(summary.get("opening_cash")):>8}',
        f'Entradas efectivo {fmt_money(summary.get("cash_in", 0)):>8}',
        f'Salidas efectivo  {fmt_money(summary.get("cash_out", 0)):>8}',
        f'Efectivo esperado {fmt_money(summary.get("expected_cash")):>8}',
        f'Efectivo contado  {fmt_money(summary.get("counted_cash")):>8}',
        f'Diferencia        {fmt_money(summary.get("difference")):>8}',
    ]

    # Tickets despachados que quedaron pendientes de cobro: no son efectivo
    # del turno, pero hay que dejarlos escritos para el siguiente turno.
    pending_total = float(summary.get('pending_total') or 0)
    if int(summary.get('pending_count') or 0) > 0:
        rows.append(thin)
        rows.append(f'PENDIENTES DE COBRO: {summary.get("pending_count")} '
                    f'({fmt_money(pending_total)})')
        rows.append('  Se cobran en el siguiente turno; no forman parte de este corte.')

    # Detalle de devoluciones (salen de la caja y por eso hay que verlas).
    returns_list = summary.get('returns_list') or []
    if returns_list:
        rows.append(thin)
        rows.append('DEVOLUCIONES')
        for r in returns_list:
            rows.append(
                f'  {r.get("folio", ""):<12} {fmt_money(r.get("total")):>10}  '
                f'{r.get("method", "")}  {(r.get("reason") or "")[:24]}'
            )

    # Detalle de entradas y salidas manuales de efectivo.
    movements_list = summary.get('movements_list') or []
    if movements_list:
        rows.append(thin)
        rows.append('ENTRADAS Y SALIDAS DE EFECTIVO')
        for m in movements_list:
            sign = '+' if m.get('kind') == 'ingreso' else '-'
            rows.append(
                f'  {sign}{fmt_money(m.get("amount")):>10}  '
                f'{(m.get("reason") or "")[:28]}'
            )

    # Conteo físico por denominación y retiro sugerido para el día siguiente.
    denoms = summary.get('denoms') or []
    if denoms:
        fondo = float(config.get('rules', {}).get('fondo_maximo', 1400) or 1400)
        total = _cents(sum(float(d.get('amount') or 0) for d in denoms))
        rows.append(line)
        rows.append('CONTEO POR DENOMINACION')
        for d in denoms:
            label = _denom_label(float(d.get('value') or 0))
            rows.append(
                f'  {label:<13}x {int(d.get("qty") or 0):>4} = {fmt_money(d.get("amount")):>10}'
            )
        rows.append(f'Total contado   {fmt_money(total):>10}')
        retiro, detalle, fondo_final = _suggest_withdrawal(denoms, fondo)
        if retiro > 0:
            rows.append(thin)
            rows.append(f'RETIRO SUGERIDO (fondo max. {fmt_money(fondo)})')
            rows.append(f'  Retirar       {fmt_money(retiro):>10}')
            for value, qty, subtotal in detalle:
                rows.append(f'  {qty} x {_denom_label(value):<11}= {fmt_money(subtotal):>10}')
            rows.append(f'  Fondo en caja {fmt_money(fondo_final):>10}')
        else:
            rows.append('No es necesario retirar efectivo.')

    notes = (summary.get('notes') or '').strip()
    if notes:
        rows.append(thin)
        rows.append('NOTAS DEL CIERRE')
        for chunk in textwrap.wrap(notes, width=max(20, width - 2)) or [notes]:
            rows.append(f'  {chunk}')

    rows.extend([line, '', ''])
    return '\n'.join(rows)


def build_shift_lines(summary, config):
    """Líneas tipadas del corte de caja para impresión ESC/POS a doble tamaño."""
    width = max(20, int(config.get('printer', {}).get('width', 42) or 42) // 2)
    store = config.get('store', {})
    line = '=' * width
    thin = '-' * width
    lines = [
        ('normal', line),
        ('bold_center', (store.get('name') or 'La Colonial').upper()),
        ('bold_center', 'CORTE DE CAJA'),
        ('normal', line),
        ('normal', f'Turno  : #{summary["id"]}'),
        ('normal', f'Cajero : {(summary.get("user_name") or "")[:width - 9]}'),
        ('normal', f'Abierto: {str(summary.get("opened_at") or "")[:width - 9]}'),
        ('normal', f'Cerrado: {str(summary.get("closed_at") or "")[:width - 9]}'),
        ('normal', thin),
        ('normal', f'Tickets     {summary.get("sales_count", 0):>9}'),
        ('normal', f'Ventas      {fmt_money(summary.get("sales_total")):>9}'),
        ('normal', f'Cancelados  {summary.get("cancelled_count", 0):>9}'),
        ('normal', f'Devoluc.    {summary.get("returns_count", 0):>9}'),
        ('normal', f'Por cobrar  {summary.get("pending_count", 0):>9}'),
        ('normal', thin),
        ('normal', f'Efectivo    {fmt_money(summary.get("by_method", {}).get("efectivo", 0)):>9}'),
        ('normal', f'Tarjeta     {fmt_money(summary.get("by_method", {}).get("tarjeta", 0)):>9}'),
        ('normal', f'Transf.     {fmt_money(summary.get("by_method", {}).get("transferencia", 0)):>9}'),
        ('normal', thin),
        ('normal', f'Fondo ini.  {fmt_money(summary.get("opening_cash")):>9}'),
        ('normal', f'Entradas    {fmt_money(summary.get("cash_in", 0)):>9}'),
        ('normal', f'Salidas     {fmt_money(summary.get("cash_out", 0)):>9}'),
        ('normal', f'Esperado    {fmt_money(summary.get("expected_cash")):>9}'),
        ('normal', f'Contado     {fmt_money(summary.get("counted_cash")):>9}'),
        ('normal', f'Diferencia  {fmt_money(summary.get("difference")):>9}'),
    ]

    pending_total = float(summary.get('pending_total') or 0)
    if int(summary.get('pending_count') or 0) > 0:
        lines.append(('normal', thin))
        lines.append(('normal', f'PENDIENTES: {summary.get("pending_count")} ({fmt_money(pending_total)})'))

    returns_list = summary.get('returns_list') or []
    if returns_list:
        lines.append(('normal', thin))
        lines.append(('bold_center', 'DEVOLUCIONES'))
        for r in returns_list:
            lines.append(('normal', f'{r.get("folio", "")} {fmt_money(r.get("total"))}'))

    movements_list = summary.get('movements_list') or []
    if movements_list:
        lines.append(('normal', thin))
        lines.append(('bold_center', 'ENTRADAS / SALIDAS'))
        for m in movements_list:
            sign = '+' if m.get('kind') == 'ingreso' else '-'
            lines.append(('normal', f'{sign}{fmt_money(m.get("amount"))} {(m.get("reason") or "")[:width - 11]}'))

    denoms = summary.get('denoms') or []
    if denoms:
        fondo = float(config.get('rules', {}).get('fondo_maximo', 1400) or 1400)
        total = _cents(sum(float(d.get('amount') or 0) for d in denoms))
        lines.append(('normal', line))
        lines.append(('bold_center', 'CONTEO DENOMINACIONES'))
        for d in denoms:
            label = _denom_label(float(d.get('value') or 0))
            lines.append(('normal', f'{label:<7}x{int(d.get("qty") or 0):>3}={fmt_money(d.get("amount")):>9}'))
        lines.append(('big_bold', 'TOTAL CONTADO'))
        lines.append(('big_bold', fmt_money(total)))
        retiro, detalle, fondo_final = _suggest_withdrawal(denoms, fondo)
        if retiro > 0:
            lines.append(('normal', thin))
            lines.append(('big_bold', 'RETIRO SUGERIDO'))
            lines.append(('big_bold', fmt_money(retiro)))
            for value, qty, subtotal in detalle:
                lines.append(('normal', f'{qty}x{_denom_label(value):<8}={fmt_money(subtotal):>9}'))
            lines.append(('big_bold', 'FONDO FINAL EN CAJA'))
            lines.append(('big_bold', fmt_money(fondo_final)))
        else:
            lines.append(('normal', 'Sin retiro necesario.'))

    notes = (summary.get('notes') or '').strip()
    if notes:
        lines.append(('normal', thin))
        lines.append(('normal', 'NOTAS:'))
        for chunk in textwrap.wrap(notes, width=width) or [notes]:
            lines.append(('normal', chunk))

    lines.append(('normal', line))
    return lines


DENOM_LABELS = {
    0.5: '50 centavos',
    1: '$1',
    2: '$2',
    5: '$5',
    10: '$10',
    20: '$20',
    50: '$50',
    100: '$100',
    200: '$200',
    500: '$500',
    1000: '$1,000',
}

BILL_VALUES = (1000, 500, 200, 100, 50, 20)


def _denom_label(value):
    return DENOM_LABELS.get(round(value, 2), fmt_money(value))


def _suggest_withdrawal(denoms, limit):
    """Billetes a retirar (de mayor a menor) para dejar cuando mucho `limit` en caja."""
    total = _cents(sum(float(d.get('amount') or 0) for d in denoms))
    to_retire = _cents(total - max(0.0, float(limit or 0)))
    if to_retire <= 0:
        return 0.0, [], total
    available = {
        round(float(d.get('value') or 0), 2): int(d.get('qty') or 0)
        for d in denoms
    }
    remaining = to_retire
    detalle = []
    for value in BILL_VALUES:
        if remaining <= 0:
            break
        qty = min(available.get(value, 0), int(remaining // value))
        if qty > 0:
            subtotal = _cents(value * qty)
            detalle.append((value, qty, subtotal))
            remaining = _cents(remaining - subtotal)
    retirado = _cents(to_retire - remaining)
    return retirado, detalle, _cents(total - retirado)


def build_return_ticket(return_record, sale, config):
    """Comprobante de devolución (ticket interno) en texto plano."""
    width = int(config.get('printer', {}).get('width', 42) or 42)
    return render_ticket_text(build_return_ticket_lines(return_record, sale, config), width)


def build_return_ticket_lines(return_record, sale, config):
    """Comprobante de devolución como lista de (tipo, texto)."""
    width = int(config.get('printer', {}).get('width', 42) or 42)
    store = config.get('store', {})
    name = (store.get('name') or 'LA COLONIAL').strip()
    rfc = (store.get('rfc') or '').strip()
    address = (store.get('address') or '').strip()
    phone = (store.get('phone') or '').strip()
    footer = (store.get('footer') or '¡Gracias por su compra!').strip()

    lines = []
    line = '=' * width
    thin = '-' * width
    if os.path.exists(LOGO_PATH):
        lines.append(('logo', ''))
    for chunk in _wrap(name.upper(), width):
        lines.append(('store_name', chunk))
    if rfc:
        lines.append(('text', f'RFC: {rfc}'.center(width)))
    if store.get('show_address', True):
        for chunk in _wrap(address, width) if address else []:
            lines.append(('text', chunk.center(width)))
        if phone:
            lines.append(('text', f'Tel: {phone}'.center(width)))
    lines.append(('text', 'DEVOLUCIÓN'.center(width)))
    lines.append(('text', line))
    lines.append(('text', f'Devolución: {return_record["folio"]}'))
    lines.append(('text', f'Ticket     : {sale["folio"]}'))
    created = return_record.get('created_at') or ''
    try:
        created = datetime.datetime.fromisoformat(created).strftime('%d/%m/%Y %H:%M')
    except ValueError:
        pass
    lines.append(('text', f'Fecha     : {created}'))
    lines.append(('text', f'Cajero    : {return_record.get("user_name") or ""}'))
    lines.append(('text', thin))
    lines.append(('text', f'{"Cant":<9}{"Producto":<{width - 20}}{"Importe":>11}'))
    lines.append(('text', thin))

    name_width = width - 20
    for item in return_record.get('items', []):
        chunks = _wrap(item['name'], name_width)
        first = chunks[0] if chunks else ''
        qty_label = fmt_qty_unit(item.get('qty'), item.get('unit'))
        lines.append(('text', f'{qty_label:<9}{first:<{name_width}}{"-" + fmt_money(item["line_total"]):>11}'))
        for extra in chunks[1:]:
            lines.append(('text', f'{"":<9}{extra}'))

    lines.append(('text', line))
    lines.append(('total_label', 'TOTAL DEVUELTO'))
    lines.append(('total_amount', '-' + fmt_money(return_record['total'])))
    lines.append(('text', line))
    lines.append(('text', f'Forma: {METHOD_LABEL.get(return_record.get("method"), return_record.get("method"))}'))
    if return_record.get('reason'):
        lines.append(('text', 'Motivo:'))
        for chunk in _wrap(return_record['reason'], width - 8):
            lines.append(('text', f'  {chunk}'))
    if return_record.get('status') == 'cancelled':
        lines.append(('text', line))
        lines.append(('text', '*** DEVOLUCIÓN ANULADA ***'.center(width)))
    lines.append(('text', line))
    for chunk in _wrap(footer, width):
        lines.append(('text', chunk.center(width)))
    lines.append(('text', ''))
    lines.append(('text', ''))
    return lines


def build_test_ticket(config):
    width = int(config.get('printer', {}).get('width', 42) or 42)
    line = '=' * width
    now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    return '\n'.join([
        line,
        (config.get('store', {}).get('name') or 'La Colonial').upper().center(width),
        'PRUEBA DE IMPRESORA'.center(width),
        line,
        now.center(width),
        'Si lees este ticket, la impresora',
        'esta lista para cobrar.',
        line,
        '',
        '',
    ])


def build_test_lines(config):
    """Ticket de prueba con el logo, nombre y TOTAL en el mismo estilo de las ventas."""
    width = int(config.get('printer', {}).get('width', 42) or 42)
    store = config.get('store', {})
    name = (store.get('name') or 'LA COLONIAL').strip().upper()
    rfc = (store.get('rfc') or '').strip()
    address = (store.get('address') or '').strip()
    phone = (store.get('phone') or '').strip()
    line = '=' * width
    now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    lines = []
    if os.path.exists(LOGO_PATH):
        lines.append(('logo', ''))
    lines.append(('store_name', name))
    if rfc:
        lines.append(('text', f'RFC: {rfc}'.center(width)))
    if address:
        lines.append(('text', address.center(width)))
    if phone:
        lines.append(('text', f'Tel: {phone}'.center(width)))
    lines.extend([
        ('text', line),
        ('text', 'PRUEBA DE IMPRESORA'.center(width)),
        ('text', now.center(width)),
        ('text', 'Si lees este ticket, la impresora'),
        ('text', 'esta lista para cobrar.'),
        ('text', line),
        ('total_label', 'TOTAL'),
        ('total_amount', '$1,234.56'),
        ('text', line),
        ('text', ''),
    ])
    return lines


def print_diagnostic(config, amount='$1,234.56'):
    """Imprime una hoja con 4 formas de imprimir el TOTAL, para ver cuál
    entiende la impresora. Devuelve (ok, error)."""
    printer = config.get('printer', {})
    parts = [b'\x1b@', b'\x1b\x74\x02']
    matrix = render_raster(amount, 6)

    def label(text):
        parts.append(text.encode('cp850', 'replace') + b'\n')

    label('--- A: raster, bit de arriba = 1 ---')
    parts.append(encode_raster(matrix, msb_top=True))
    parts.append(b'\n')
    label('--- B: raster, medidas en puntos ---')
    parts.append(encode_raster(matrix, header_dots=True))
    parts.append(b'\n')
    label('--- C: doble + negritas ---')
    parts.append(styled_amount(amount))
    label('--- D: doble + negritas + doble golpe ---')
    parts.append(styled_amount(amount, double_strike=True))
    parts.append(b'\x1d\x56\x42\x00')
    return print_raw(b''.join(parts), resolve_printer(config))


def print_test(config):
    """Imprime el ticket de prueba (con el TOTAL en el estilo de las ventas)."""
    lines = build_test_lines(config)
    width = int(config.get('printer', {}).get('width', 42) or 42)
    text = render_ticket_text(lines, width)
    printer = resolve_printer(config)
    data = escpos_lines(lines, config, cut=True, drawer=False)
    ok, error = print_raw(data, printer)
    return {'ok': ok, 'printer': printer, 'error': error, 'ticket_text': text}


# ---------------------------------------------------------------- TOTAL en bitmap
#
# El TOTAL no se imprime como texto con doble tamaño (ESC/POS sólo admite 2x2):
# se dibuja como imagen de puntos (GS v 0) a partir de una fuente 5x7 escalada.
# Escalar x6 significa trazos de 6 píxeles: el número sale 6 veces más grueso
# (y mucho más grande) que con el doble tamaño normal.

# Fuente de puntos 5x7 (1 = píxel). Sólo los caracteres que usa el POS.
FONT_5X7 = {
    ' ': ('00000', '00000', '00000', '00000', '00000', '00000', '00000'),
    '0': ('01110', '10001', '10011', '10101', '11001', '10001', '01110'),
    '1': ('00100', '01100', '00100', '00100', '00100', '00100', '01110'),
    '2': ('01110', '10001', '00001', '00010', '00100', '01000', '11111'),
    '3': ('11111', '00010', '00100', '00010', '00001', '10001', '01110'),
    '4': ('00010', '00110', '01010', '10010', '11111', '00010', '00010'),
    '5': ('11111', '10000', '11110', '00001', '00001', '10001', '01110'),
    '6': ('00110', '01000', '10000', '11110', '10001', '10001', '01110'),
    '7': ('11111', '00001', '00010', '00100', '01000', '01000', '01000'),
    '8': ('01110', '10001', '10001', '01110', '10001', '10001', '01110'),
    '9': ('01110', '10001', '10001', '01111', '00001', '00010', '01100'),
    'A': ('01110', '10001', '10001', '11111', '10001', '10001', '10001'),
    'B': ('11110', '10001', '10001', '11110', '10001', '10001', '11110'),
    'C': ('01110', '10001', '10000', '10000', '10000', '10001', '01110'),
    'D': ('11110', '10001', '10001', '10001', '10001', '10001', '11110'),
    'E': ('11111', '10000', '10000', '11110', '10000', '10000', '11111'),
    'F': ('11111', '10000', '10000', '11110', '10000', '10000', '10000'),
    'G': ('01110', '10001', '10000', '10111', '10001', '10001', '01111'),
    'H': ('10001', '10001', '10001', '11111', '10001', '10001', '10001'),
    'I': ('01110', '00100', '00100', '00100', '00100', '00100', '01110'),
    'J': ('00111', '00010', '00010', '00010', '00010', '10010', '01100'),
    'K': ('10001', '10010', '10100', '11000', '10100', '10010', '10001'),
    'L': ('10000', '10000', '10000', '10000', '10000', '10000', '11111'),
    'M': ('10001', '11011', '10101', '10101', '10001', '10001', '10001'),
    'N': ('10001', '11001', '10101', '10011', '10001', '10001', '10001'),
    'O': ('01110', '10001', '10001', '10001', '10001', '10001', '01110'),
    'P': ('11110', '10001', '10001', '11110', '10000', '10000', '10000'),
    'Q': ('01110', '10001', '10001', '10001', '10101', '10010', '01101'),
    'R': ('11110', '10001', '10001', '11110', '10100', '10010', '10001'),
    'S': ('01111', '10000', '10000', '01110', '00001', '00001', '11110'),
    'T': ('11111', '00100', '00100', '00100', '00100', '00100', '00100'),
    'U': ('10001', '10001', '10001', '10001', '10001', '10001', '01110'),
    'V': ('10001', '10001', '10001', '10001', '10001', '01010', '00100'),
    'W': ('10001', '10001', '10001', '10101', '10101', '10101', '01010'),
    'X': ('10001', '10001', '01010', '00100', '01010', '10001', '10001'),
    'Y': ('10001', '10001', '01010', '00100', '00100', '00100', '00100'),
    'Z': ('11111', '00001', '00010', '00100', '01000', '10000', '11111'),
    '$': ('00100', '01111', '10100', '01110', '00101', '11110', '00100'),
    '.': ('00000', '00000', '00000', '00000', '00000', '01100', '01100'),
    ',': ('00000', '00000', '00000', '00000', '01100', '00100', '01000'),
    '-': ('00000', '00000', '00000', '11111', '00000', '00000', '00000'),
    '+': ('00000', '00100', '00100', '11111', '00100', '00100', '00000'),
    ':': ('00000', '01100', '01100', '00000', '01100', '01100', '00000'),
    '?': ('01110', '10001', '00001', '00010', '00100', '00000', '00100'),
}

# Puntos de avance de una columna de la fuente normal (12x17 a 203 ppp).
DOTS_PER_CHAR = 12

# Ancho (en caracteres) con el que se dibuja el marco del ticket.
def frame_dots(printer):
    return int(printer.get('width', 42) or 42) * DOTS_PER_CHAR


def raster_width(text, scale):
    """Ancho en píxeles de `text` dibujado con la fuente 5x7 escalada."""
    n = len(str(text))
    if n <= 0:
        return 0
    return scale * (6 * n - 1)  # 5 columnas + 1 de separación


def render_raster(text, scale):
    """Dibuja `text` y devuelve la matriz de píxeles (filas de 0/1)."""
    scale = max(1, int(scale))
    chars = [str(c).upper() for c in str(text)]
    glyphs = [FONT_5X7.get(c, FONT_5X7['?']) for c in chars]
    width = raster_width(str(text), scale)
    height = 7 * scale
    matrix = [[0] * width for _ in range(height)]
    x = 0
    for glyph in glyphs:
        for gy, row in enumerate(glyph):
            for gx, cell in enumerate(row):
                if cell != '1':
                    continue
                for sy in range(scale):
                    line = matrix[gy * scale + sy]
                    for sx in range(scale):
                        line[x + gx * scale + sx] = 1
        x += 6 * scale
    return matrix


def encode_raster(matrix, msb_top=False, header_dots=False):
    """Codifica la matriz en el comando ESC/POS GS v 0 (imagen de puntos).

    msb_top   -> el bit 7 es el punto de arriba (al revés que el estándar).
    header_dots -> xL/yL en puntos en lugar de bytes.
    """
    height = len(matrix)
    width = len(matrix[0]) if height else 0
    width_bytes = (width + 7) // 8
    bands = (height + 7) // 8
    data = bytearray()
    for band in range(bands):
        for col in range(width_bytes):
            byte = 0
            for bit in range(8):
                y = band * 8 + bit
                if y >= height:
                    break
                row = matrix[y]
                x = col * 8 + bit
                if x < width and row[x]:
                    byte |= (1 << (7 - bit)) if msb_top else (1 << bit)
            data.append(byte)
    if header_dots:
        h_size, v_size = width, height
    else:
        h_size, v_size = width_bytes, bands
    header = (b'\x1d\x76\x30\x00'
              + bytes([h_size & 0xFF, (h_size >> 8) & 0xFF,
                       v_size & 0xFF, (v_size >> 8) & 0xFF]))
    return header + bytes(data)


def fit_scale(text, printer, max_scale=6):
    """Mayor escala con la que el texto cabe en el ancho del ticket."""
    limit = frame_dots(printer) - 16
    for scale in range(max_scale, 1, -1):
        if raster_width(text, scale) <= limit:
            return scale
    return 2


def raster_line(text, scale, printer):
    """Dibuja `text` centrado en el ancho del ticket."""
    matrix = render_raster(text, scale)
    return encode_gs_v0(matrix, total_dots=frame_dots(printer))


def styled_amount(text, double_strike=False):
    """Importe en doble tamaño y negritas.

    Con `double_strike` se imprime dos veces desplazado 1/180" (un punto):
    el trazo de la negrita (2 puntos) se solapa y queda de 3 puntos de grosor.
    """
    data = str(text).encode('cp850', 'replace')
    parts = [b'\x1b\x61\x01',          # centrar
             b'\x1b\x45\x01',          # negritas
             b'\x1d\x21\x03']          # doble alto y doble ancho
    if double_strike:
        parts.append(b'\x1b\x03\x01')  # espaciado de línea 1/180"
        parts.append(data)
        parts.append(b'\n')
        parts.append(data)             # segunda pasada, un punto más abajo
        parts.append(b'\n')
        parts.append(b'\x1b\x02')      # volver al espaciado normal
    else:
        parts.append(data)
        parts.append(b'\n')
    parts.append(b'\x1d\x21\x00')      # tamaño normal
    parts.append(b'\x1b\x45\x00')      # sin negritas
    parts.append(b'\x1b\x61\x00')      # izquierda
    return b''.join(parts)


def _decode_png_rgb(path):
    """Decodifica PNG 8-bit RGB/RGBA/Grayscale no entrelazado en Python puro."""
    with open(path, 'rb') as fh:
        if fh.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError('No es un archivo PNG válido.')
        idat = bytearray()
        w = h = bd = ct = 0
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            length, ctype = struct.unpack('>I4s', hdr)
            data = fh.read(length)
            fh.read(4)
            if ctype == b'IHDR':
                w, h, bd, ct, _, _, inter = struct.unpack('>IIBBBBB', data)
                if bd != 8 or inter != 0 or ct not in (0, 2, 4, 6):
                    raise ValueError('Formato PNG no soportado para ticket.')
            elif ctype == b'IDAT':
                idat.extend(data)
            elif ctype == b'IEND':
                break
    raw = zlib.decompress(bytes(idat))
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    stride = w * bpp
    rows = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        ftype = raw[pos]
        pos += 1
        cur = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 0xFF
        rows.append(cur)
        prev = cur
    return w, h, bpp, ct, rows


def _pixel_lum(row, x, bpp, ct):
    i = x * bpp
    if ct == 0:
        return row[i]
    if ct == 4:
        g, a = row[i], row[i + 1]
        return 255 - (((255 - g) * a) // 255)
    r, g, b = row[i], row[i + 1], row[i + 2]
    lum = (r * 299 + g * 587 + b * 114) // 1000
    if ct == 6:
        a = row[i + 3]
        return 255 - (((255 - lum) * a) // 255)
    return lum


def load_logo_matrix(path=LOGO_PATH, max_width=224):
    """Carga y escala el logo a matriz monocromática (0/1) lista para GS v 0."""
    target_w = max(64, (int(max_width) // 8) * 8)
    key = (path, target_w)
    if key in _LOGO_CACHE:
        return _LOGO_CACHE[key]
    if not os.path.exists(path):
        return None
    try:
        w, h, bpp, ct, rows = _decode_png_rgb(path)
    except Exception:
        return None
    minx, miny, maxx, maxy = w, h, 0, 0
    for y in range(h):
        row = rows[y]
        for x in range(w):
            if _pixel_lum(row, x, bpp, ct) < 235:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < minx or maxy < miny:
        return None
    cw = maxx - minx + 1
    ch = maxy - miny + 1
    target_h = max(1, round(ch * target_w / cw))
    matrix = []
    for ty in range(target_h):
        y0 = miny + (ty * ch) // target_h
        y1 = min(maxy + 1, max(y0 + 1, miny + ((ty + 1) * ch) // target_h))
        out_row = []
        for tx in range(target_w):
            x0 = minx + (tx * cw) // target_w
            x1 = min(maxx + 1, max(x0 + 1, minx + ((tx + 1) * cw) // target_w))
            total_lum = 0
            count = 0
            for sy in range(y0, y1):
                srow = rows[sy]
                for sx in range(x0, x1):
                    total_lum += _pixel_lum(srow, sx, bpp, ct)
                    count += 1
            avg_lum = total_lum // max(1, count)
            out_row.append(1 if avg_lum < 205 else 0)
        matrix.append(out_row)
    _LOGO_CACHE[key] = matrix
    return matrix


def encode_gs_v0(matrix, total_dots=0):
    """Codifica una matriz (filas de 0/1) en bloques ESC/POS GS v 0 centrados.

    Se divide en tiras de 24 filas para no saturar el buffer de impresoras
    térmicas económicas, y se centra con margen izquierdo en bytes.
    """
    height = len(matrix)
    width = len(matrix[0]) if height else 0
    if not height or not width:
        return b''
    img_bytes = (width + 7) // 8
    pad_bytes = max(0, (int(total_dots) - width) // 16) if total_dots > width else 0
    width_bytes = pad_bytes + img_bytes
    chunks = []
    strip_h = 24
    for y0 in range(0, height, strip_h):
        y1 = min(height, y0 + strip_h)
        h_strip = y1 - y0
        data = bytearray()
        for y in range(y0, y1):
            row = matrix[y]
            if pad_bytes:
                data.extend(b'\x00' * pad_bytes)
            for bx in range(img_bytes):
                byte = 0
                base_x = bx * 8
                for bit in range(8):
                    x = base_x + bit
                    if x < width and row[x]:
                        byte |= (1 << (7 - bit))
                data.append(byte)
        header = (b'\x1d\x76\x30\x00'
                  + bytes([width_bytes & 0xFF, (width_bytes >> 8) & 0xFF,
                           h_strip & 0xFF, (h_strip >> 8) & 0xFF]))
        chunks.append(header + bytes(data))
    return b''.join(chunks)


# ---------------------------------------------------------------- ESC/POS

def escpos_bytes(text, config, cut=True, drawer=False):
    printer = config.get('printer', {})
    parts = [b'\x1b@', b'\x1b\x74\x02']  # init + CP850
    if drawer and printer.get('open_drawer', True):
        parts.append(b'\x1b\x70\x00\x19\xfa')
    parts.append(text.encode('cp850', 'replace'))
    parts.append(b'\n\n')
    if cut:
        parts.append(b'\x1d\x56\x42\x00')
    return b''.join(parts)


def escpos_lines(lines, config, cut=True, drawer=False):
    """Bytes ESC/POS a partir de las líneas tipadas del ticket.

    'total_label'  -> 'TOTAL' en negritas y doble alto
    'total_amount' -> importe en doble alto y doble ancho (o bitmap, ver abajo)
    """
    printer = config.get('printer', {})
    parts = [b'\x1b@', b'\x1b\x74\x02']  # init + CP850
    if drawer and printer.get('open_drawer', True):
        parts.append(b'\x1b\x70\x00\x19\xfa')

    # Estilo del total: 'raster' (imagen de puntos: trazo N veces más grueso),
    # 'strike' (doble tamaño + negritas impreso dos veces: trazo de 3 puntos)
    # o 'escpos' (doble tamaño + negritas, una sola vez).
    style = str(printer.get('total_style', 'raster')).lower()
    amount_text = ''
    for kind, text in lines:
        if kind == 'total_amount':
            amount_text = str(text)
            break
    amount_scale = fit_scale(amount_text, printer) if amount_text else 4
    # Escala al doble del grosor: label y amount se imprimen con el doble
    # de píxeles de trazo que en la versión anterior.
    amount_scale = min(8, amount_scale * 2)
    label_scale = max(4, min(8, amount_scale - 2))

    for kind, text in lines:
        if kind == 'logo':
            total_dots = frame_dots(printer)
            max_w = min(224, max(128, total_dots - 32))
            matrix = load_logo_matrix(LOGO_PATH, max_w)
            if matrix:
                parts.append(encode_gs_v0(matrix, total_dots=total_dots))
                parts.append(b'\n')
            continue
        data = str(text).encode('cp850', 'replace')
        if kind == 'store_name':
            parts.append(b'\x1b\x61\x01')   # centrar
            parts.append(b'\x1b\x45\x01')   # negritas
            parts.append(data)
            parts.append(b'\x1b\x45\x00')   # sin negritas
            parts.append(b'\x1b\x61\x00')   # izquierda
            parts.append(b'\n')
            continue
        if kind == 'total_label' and style == 'raster':
            parts.append(raster_line(str(text), label_scale, printer))
            parts.append(b'\n')
            continue
        if kind == 'total_amount' and style == 'raster':
            parts.append(raster_line(str(text), amount_scale, printer))
            parts.append(b'\n')
            continue
        if kind == 'total_amount' and style == 'strike':
            parts.append(styled_amount(str(text), double_strike=True))
            continue
        if kind == 'total_label':
            parts.append(b'\x1b\x61\x01')   # centrar
            parts.append(b'\x1b\x45\x01')   # negritas
            parts.append(b'\x1d\x21\x02')   # doble alto
            parts.append(data)
            parts.append(b'\x1d\x21\x00')   # tamaño normal
            parts.append(b'\x1b\x45\x00')   # sin negritas
            parts.append(b'\x1b\x61\x00')   # izquierda
        elif kind == 'total_amount':
            parts.append(b'\x1b\x61\x01')   # centrar
            parts.append(b'\x1d\x21\x03')   # doble alto y doble ancho
            parts.append(b'\x1b\x45\x01')   # negritas
            parts.append(data)
            parts.append(b'\x1d\x21\x00')   # tamaño normal
            parts.append(b'\x1b\x45\x00')   # sin negritas
            parts.append(b'\x1b\x61\x00')   # izquierda
        else:
            parts.append(data)
        parts.append(b'\n')
    parts.append(b'\n')
    if cut:
        parts.append(b'\x1d\x56\x42\x00')
    return b''.join(parts)


def save_ticket_copy(text, folio, kind='ticket'):
    day = datetime.date.today().isoformat()
    folder = os.path.join(TICKET_DIR, day)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{kind}_{folio}.txt')
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path
    except OSError:
        return None


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _spool = ctypes.WinDLL('winspool.drv', use_last_error=True)

    class _DOC_INFO_1(ctypes.Structure):
        _fields_ = [
            ('pDocName', wintypes.LPWSTR),
            ('pOutputFile', wintypes.LPWSTR),
            ('pDatatype', wintypes.LPWSTR),
        ]

    class _PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [
            ('pPrinterName', wintypes.LPWSTR),
            ('pServerName', wintypes.LPWSTR),
            ('Attributes', wintypes.DWORD),
        ]

    _spool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    _spool.OpenPrinterW.restype = wintypes.BOOL
    _spool.ClosePrinter.argtypes = [wintypes.HANDLE]
    _spool.ClosePrinter.restype = wintypes.BOOL
    _spool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_DOC_INFO_1)]
    _spool.StartDocPrinterW.restype = wintypes.DWORD
    _spool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    _spool.EndDocPrinter.restype = wintypes.BOOL
    _spool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    _spool.StartPagePrinter.restype = wintypes.BOOL
    _spool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    _spool.EndPagePrinter.restype = wintypes.BOOL
    _spool.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD)]
    _spool.WritePrinter.restype = wintypes.BOOL
    _spool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _spool.GetDefaultPrinterW.restype = wintypes.BOOL
    _spool.EnumPrintersW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    _spool.EnumPrintersW.restype = wintypes.BOOL


def list_printers():
    if os.name != 'nt':
        return []
    flags = 6  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed = wintypes.DWORD(0)
    returned = wintypes.DWORD(0)
    _spool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if not needed.value:
        return []
    buffer = ctypes.create_string_buffer(needed.value + 16)
    ok = _spool.EnumPrintersW(flags, None, 4, buffer, needed.value,
                              ctypes.byref(needed), ctypes.byref(returned))
    if not ok:
        return []
    array = ctypes.cast(buffer, ctypes.POINTER(_PRINTER_INFO_4))
    names = []
    for i in range(returned.value):
        try:
            names.append(array[i].pPrinterName or '')
        except (ValueError, OSError):
            pass
    return [n for n in names if n]


def default_printer():
    if os.name != 'nt':
        return ''
    size = wintypes.DWORD(0)
    _spool.GetDefaultPrinterW(None, ctypes.byref(size))
    if not size.value:
        return ''
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _spool.GetDefaultPrinterW(buffer, ctypes.byref(size)):
        return ''
    return buffer.value


VIRTUAL_PRINTERS = (
    'microsoft print to pdf',
    'microsoft xps document writer',
    'fax',
    'onenote',
)


def resolve_printer(config):
    configured = (config.get('printer', {}).get('name') or '').strip()
    installed = list_printers()
    if configured:
        if not installed or configured in installed:
            return configured
        lower_map = {p.lower(): p for p in installed}
        if configured.lower() in lower_map:
            return lower_map[configured.lower()]
    dflt = default_printer()
    if dflt and (not installed or dflt in installed):
        return dflt
    for p in installed:
        if not any(v in p.lower() for v in VIRTUAL_PRINTERS):
            return p
    return installed[0] if installed else configured


def print_raw(data, printer_name):
    """Envía bytes directamente a la impresora. Devuelve (ok, error)."""
    if os.name != 'nt':
        return False, 'La impresión directa solo está disponible en Windows.'
    if not printer_name:
        return False, 'No hay impresora configurada y Windows no tiene predeterminada.'
    handle = wintypes.HANDLE()
    if not _spool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        return False, f'No se pudo abrir la impresora "{printer_name}" (error {ctypes.get_last_error()}).'
    try:
        doc = _DOC_INFO_1('Ticket La Colonial', None, 'RAW')
        if not _spool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            return False, f'No se pudo iniciar el trabajo de impresión (error {ctypes.get_last_error()}).'
        try:
            if not _spool.StartPagePrinter(handle):
                return False, 'No se pudo iniciar la página de impresión.'
            buffer = ctypes.create_string_buffer(data, len(data))
            written = wintypes.DWORD(0)
            if not _spool.WritePrinter(handle, buffer, len(data), ctypes.byref(written)):
                return False, f'Windows rechazó los datos de impresión (error {ctypes.get_last_error()}).'
            _spool.EndPagePrinter(handle)
        finally:
            _spool.EndDocPrinter(handle)
        return True, ''
    finally:
        _spool.ClosePrinter(handle)


def print_text(text, config, cut=True, drawer=False):
    printer = resolve_printer(config)
    data = escpos_bytes(text, config, cut=cut, drawer=drawer)
    ok, error = print_raw(data, printer)
    return {'ok': ok, 'printer': printer, 'error': error}


def print_sale(sale, config):
    lines = build_ticket_lines(sale, config)
    width = int(config.get('printer', {}).get('width', 42) or 42)
    text = render_ticket_text(lines, width)
    copy = save_ticket_copy(text, sale['folio'].replace('/', '-'), 'ticket')
    result = {'ticket_text': text, 'copy_path': copy, 'printer': '', 'error': '', 'ok': False}
    if not config.get('printer', {}).get('print_sale', True):
        result['error'] = 'La impresión automática está desactivada.'
        return result
    printer = resolve_printer(config)
    data = escpos_lines(lines, config, cut=True, drawer=True)
    ok, error = print_raw(data, printer)
    result.update({'ok': ok, 'printer': printer, 'error': error})
    return result


def escpos_shift_lines(lines, config, cut=True, drawer=False):
    """Bytes ESC/POS para el corte de caja: todo en doble tamaño (GS ! 0x11)
    y las secciones destacadas en mapa de bits (más grande y grueso).
    """
    printer = config.get('printer', {})
    parts = [b'\x1b@', b'\x1b\x74\x02']  # init + CP850
    if drawer and printer.get('open_drawer', True):
        parts.append(b'\x1b\x70\x00\x19\xfa')

    for kind, text in lines:
        if kind == 'big_bold':
            scale = min(4, max(2, fit_scale(str(text), printer)))
            parts.append(raster_line(str(text), scale, printer))
            parts.append(b'\n')
            continue
        data = str(text).encode('cp850', 'replace')
        parts.append(b'\x1d\x21\x11')  # doble ancho y doble alto para todo el corte
        if kind == 'bold_center':
            parts.append(b'\x1b\x61\x01')  # centrar
            parts.append(b'\x1b\x45\x01')  # negritas
            parts.append(data)
            parts.append(b'\x1b\x45\x00')
            parts.append(b'\x1b\x61\x00')
        else:
            parts.append(data)
        parts.append(b'\x1d\x21\x00')
        parts.append(b'\n')

    parts.append(b'\n\n\n')
    if cut:
        parts.append(b'\x1d\x56\x42\x00')
    return b''.join(parts)


def print_shift(summary, config):
    text = build_shift_report(summary, config)
    copy = save_ticket_copy(text, f'turno-{summary["id"]}', 'corte')
    result = {'ticket_text': text, 'copy_path': copy, 'printer': '', 'error': '', 'ok': False}
    if not config.get('printer', {}).get('print_shift', True):
        result['error'] = 'La impresión de cortes está desactivada.'
        return result
    lines = build_shift_lines(summary, config)
    data = escpos_shift_lines(lines, config, cut=True, drawer=False)
    printer = resolve_printer(config)
    ok, error = print_raw(data, printer)
    result.update({'ok': ok, 'printer': printer, 'error': error})
    return result


def print_return(return_record, sale, config, do_print=True):
    lines = build_return_ticket_lines(return_record, sale, config)
    width = int(config.get('printer', {}).get('width', 42) or 42)
    text = render_ticket_text(lines, width)
    copy = save_ticket_copy(text, str(return_record['folio']).replace('/', '-'), 'devolucion')
    result = {'ticket_text': text, 'copy_path': copy, 'printer': '', 'error': '', 'ok': False,
              'lines': [[k, t] for k, t in lines]}
    if not do_print:
        return result
    if not config.get('printer', {}).get('print_sale', True):
        result['error'] = 'La impresión automática está desactivada.'
        return result
    printer = resolve_printer(config)
    data = escpos_lines(lines, config, cut=True, drawer=False)
    ok, error = print_raw(data, printer)
    result.update({'ok': ok, 'printer': printer, 'error': error})
    return result


def build_xico_park_lines(summary, config):
    """Líneas tipadas para el ticket de resumen del día de XICO-PARK."""
    width = int(config.get('printer', {}).get('width', 42) or 42)
    line = '=' * width
    thin = '-' * width
    totals = summary.get('totals') or {}
    lines = [
        ('text', line),
        ('store_name', 'XICO-PARK'),
        ('text', 'CONTROL DE CAJA INDEPENDIENTE'.center(width)),
        ('text', line),
        ('text', f'Fecha     : {summary.get("day") or ""}'),
        ('text', f'Impreso   : {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}'),
        ('text', thin),
    ]
    kind_label = {'entrada': 'ENT', 'salida': 'SAL', 'gasto': 'GAS'}
    movs = [m for m in (summary.get('movements') or []) if m.get('active')]
    if movs:
        lines.append(('text', 'MOVIMIENTOS DEL DIA'))
        for m in reversed(movs):
            tag = kind_label.get(m.get('kind'), 'MOV')
            amt = fmt_money(m.get('amount'))
            concept = str(m.get('concept') or '')[:max(10, width - 17)]
            lines.append(('text', f'{tag} {concept:<{width - 15}}{amt:>11}'))
        lines.append(('text', thin))
    else:
        lines.append(('text', 'Sin movimientos activos en el dia.'))
        lines.append(('text', thin))

    lines.append(('text', f'Total Entradas    {fmt_money(totals.get("entradas")):>{width - 18}}'))
    lines.append(('text', f'Total Salidas     {fmt_money(totals.get("salidas")):>{width - 18}}'))
    lines.append(('text', f'Total Gastos      {fmt_money(totals.get("gastos")):>{width - 18}}'))
    lines.append(('text', ''))
    lines.append(('total_label', 'SALIDAS + GASTOS'))
    lines.append(('total_amount', fmt_money(totals.get('salidas_gastos'))))
    lines.append(('text', thin))
    lines.append(('text', f'Saldo Neto        {fmt_money(totals.get("neto")):>{width - 18}}'))
    lines.append(('text', line))
    return lines


def print_xico_park(summary, config, do_print=True):
    lines = build_xico_park_lines(summary, config)
    width = int(config.get('printer', {}).get('width', 42) or 42)
    text = render_ticket_text(lines, width)
    data = escpos_lines(lines, config, cut=True, drawer=False)
    result = {
        'ticket_text': text,
        'lines': [[k, t] for k, t in lines],
        'raw_bytes': data,
        'printer': '',
        'error': '',
        'ok': False,
    }
    if not do_print:
        return result
    printer = resolve_printer(config)
    ok, error = print_raw(data, printer)
    result.update({'ok': ok, 'printer': printer, 'error': error})
    return result
