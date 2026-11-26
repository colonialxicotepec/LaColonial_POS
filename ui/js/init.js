'use strict';

/* ------------------------------------------------------------ eventos */
function wire() {
  // login
  $('keypad').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-key]');
    if (!btn) return;
    const key = btn.dataset.key;
    if (key === 'clear') setPin('');
    else if (key === 'back') setPin(S.pin.slice(0, -1));
    else setPin(S.pin + key);
  });
  $('loginSubmit').addEventListener('click', submitPin);
  $('bootstrapBtn').addEventListener('click', bootstrap);

  // turno
  $('openShiftBtn').addEventListener('click', openShift);
  $('openingQuick').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-open]');
    if (btn) $('openingCash').value = btn.dataset.open;
  });
  $('openingCash').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); openShift(); }
  });
  $('logoutFromShift').addEventListener('click', logout);
  if ($('shiftCheckBtn')) {
    $('shiftCheckBtn').addEventListener('click', async () => {
      await refresh();
      if (!S.shift) {
        toast('Aún no han abierto turno en Caja central.', 'bad');
      } else {
        toast('Turno detectado. Pantalla desbloqueada.', 'ok');
      }
    });
  }

  // búsqueda
  $('scan').addEventListener('input', () => {
    S.lastWarn = '';
    scheduleSearch();
  });
  $('scan').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(searchTimer); handleScanEnter(); }
    else if (e.key === 'Escape') {
      e.preventDefault();
      $('scan').value = '';
      S.search = null;
      renderResultsIdle();
    } else if (e.key === 'ArrowDown') { e.preventDefault(); selectResult(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); selectResult(-1); }
  });
  $('clearSearch').addEventListener('click', () => {
    $('scan').value = '';
    S.search = null;
    renderResultsIdle();
    focusScan();
  });
  $('results').addEventListener('click', (e) => {
    const priceBtn = e.target.closest('[data-price]');
    if (priceBtn) {
      e.stopPropagation();
      openPriceModal(Number(priceBtn.dataset.price));
      return;
    }
    const row = e.target.closest('.resultrow');
    if (!row) return;
    const product = S.results[Number(row.dataset.index)];
    addProduct(product);
  });
  $('results').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const row = e.target.closest('.resultrow');
    if (!row) return;
    e.preventDefault();
    addProduct(S.results[Number(row.dataset.index)]);
  });

  // carrito
  $('cartLines').addEventListener('click', (e) => {
    const inc = e.target.closest('[data-inc]');
    const dec = e.target.closest('[data-dec]');
    const rem = e.target.closest('[data-remove]');
    if (inc) changeQty(Number(inc.dataset.inc), 1);
    else if (dec) changeQty(Number(dec.dataset.dec), -1);
    else if (rem) removeItem(Number(rem.dataset.remove));
  });
  $('cartLines').addEventListener('focusin', (e) => {
    if (e.target.matches('.qty-input')) e.target.select();
  });
  $('cartLines').addEventListener('input', (e) => {
    if (!e.target.matches('.qty-input')) return;
    const index = Number(e.target.dataset.qty);
    const item = S.cart[index];
    if (!item) return;
    const v = num(e.target.value);
    const line = e.target.closest('.cartline');
    const totalEl = line && line.querySelector('.cl-total');
    if (totalEl) totalEl.textContent = fmtMoney(round2(item.price * v));
    const count = S.cart.map((it, i) => (i === index ? { ...it, qty: v } : it));
    const total = S.cart.reduce(
      (s, it, i) => s + round2(it.price * (i === index ? v : it.qty)), 0
    );
    $('tCount').textContent = countText(count);
    $('tTotal').textContent = fmtMoney(round2(total));
  });
  $('cartLines').addEventListener('keydown', (e) => {
    if (!e.target.matches('.qty-input')) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      commitQty(Number(e.target.dataset.qty), e.target.value);
      focusScan();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      renderCart();
      focusScan();
    }
  });
  $('cartLines').addEventListener('change', (e) => {
    if (e.target.matches('.qty-input')) {
      commitQty(Number(e.target.dataset.qty), e.target.value);
    }
  });
  $('clearCart').addEventListener('click', () => clearCart(false));
  $('chooseCustomerBtn').addEventListener('click', () => openCustomers('select'));
  $('clearCustomerBtn').addEventListener('click', () => {
    S.customer = null;
    renderCart();
    focusScan();
  });
  $('payBtn').addEventListener('click', openPayment);
  if ($('pendingBtn')) $('pendingBtn').addEventListener('click', openPendingSales);
  if ($('pendingCartBtn')) $('pendingCartBtn').addEventListener('click', openPendingSales);
  if ($('pendingRefreshBtn')) $('pendingRefreshBtn').addEventListener('click', openPendingSales);
  $('salesBtn').addEventListener('click', openSales);
  if ($('cashMoveBtn')) $('cashMoveBtn').addEventListener('click', openCashMovements);
  $('closeShiftBtn').addEventListener('click', openShiftClose);

  // pago
  document.querySelectorAll('#payModal .method').forEach((btn) => {
    btn.addEventListener('click', () => setMethod(btn.dataset.method));
  });
  $('cashQuick').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cash]');
    if (!btn) return;
    $('cashInput').value = btn.dataset.cash === 'exact'
      ? activePaymentTotal().toFixed(2) : btn.dataset.cash;
    updateChange();
    $('cashInput').focus();
  });
  $('cashInput').addEventListener('input', updateChange);
  $('cashInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmPayment(); }
  });
  $('refInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmPayment(); }
  });
  $('payConfirm').addEventListener('click', confirmPayment);
  $('payCancel').addEventListener('click', () => {
    S.payingPendingSale = null;
    closeModal('payModal');
  });
  $('newSaleBtn').addEventListener('click', () => {
    closeModal('doneModal');
    refreshQuiet();
    focusScan();
  });

  // tickets pendientes de cobro
  if ($('pendingBody')) {
    $('pendingBody').addEventListener('click', (e) => {
      const payBtn = e.target.closest('[data-paypending]');
      const cancelBtn = e.target.closest('[data-cancel]');
      const printBtn = e.target.closest('[data-print]');
      if (payBtn) {
        const id = Number(payBtn.dataset.paypending);
        const sale = (S.pendingSales || []).find((s) => Number(s.id) === id);
        if (sale) openPendingSalePayment(sale);
      } else if (cancelBtn) {
        openCancel(Number(cancelBtn.dataset.cancel), cancelBtn.dataset.folio);
      } else if (printBtn) {
        reprintSale(Number(printBtn.dataset.print), printBtn.dataset.token);
      }
    });
  }

  // ventas del día
  $('salesBody').addEventListener('click', (e) => {
    const payBtn = e.target.closest('[data-paypending]');
    const cancelBtn = e.target.closest('[data-cancel]');
    const printBtn = e.target.closest('[data-print]');
    const returnBtn = e.target.closest('[data-return]');
    if (payBtn) {
      const id = Number(payBtn.dataset.paypending);
      const sale = (S.pendingSales || []).find((s) => Number(s.id) === id);
      if (sale) openPendingSalePayment(sale);
      else {
        api('/api/pos/sales/pending').then((d) => {
          S.pendingSales = d.pending_sales || [];
          const found = S.pendingSales.find((s) => Number(s.id) === id);
          if (found) openPendingSalePayment(found);
        }).catch((err) => toast(err.message, 'bad'));
      }
    }
    else if (cancelBtn) openCancel(Number(cancelBtn.dataset.cancel), cancelBtn.dataset.folio);
    else if (printBtn) reprintSale(Number(printBtn.dataset.print), printBtn.dataset.token);
    else if (returnBtn) openReturn(Number(returnBtn.dataset.return), returnBtn.dataset.folio);
  });
  $('returnsBody').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-returncancel]');
    if (btn) openCancelReturn(Number(btn.dataset.returncancel), btn.dataset.folio);
  });
  $('exportBtn').addEventListener('click', exportSales);
  $('exportBtn2').addEventListener('click', exportSales);

  // clientes
  $('customersSearch').addEventListener('input', () => {
    clearTimeout(customerSearchTimer);
    customerSearchTimer = setTimeout(loadCustomers, 180);
  });
  $('customersSearch').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      clearTimeout(customerSearchTimer);
      loadCustomers();
    }
  });
  $('customersIncludeDeleted').addEventListener('change', loadCustomers);
  $('customersBody').addEventListener('click', (e) => {
    const detail = e.target.closest('[data-customer-detail]');
    if (detail) showCustomerDetail(Number(detail.dataset.customerDetail));
  });
  $('customerDetail').addEventListener('click', (e) => {
    const use = e.target.closest('[data-customer-use]');
    if (use) chooseCustomer(Number(use.dataset.customerUse));
  });
  $('customersCancelSelect').addEventListener('click', () => closeModal('customersModal'));

  // devoluciones
  $('returnSearch').addEventListener('click', () => lookupReturn(null));
  $('returnFolio').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); lookupReturn(null); }
  });
  $('returnLines').addEventListener('input', updateReturnTotal);
  $('returnConfirm').addEventListener('click', confirmReturn);

  // precio de emergencia
  $('priceSave').addEventListener('click', savePrice);
  $('pricesBtn').addEventListener('click', () => {
    $('priceList').hidden = !$('priceList').hidden;
    if (!$('priceList').hidden) loadPriceList();
  });
  $('priceList').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-revert]');
    if (btn) openPriceModal(btn.dataset.revert, 'revert');
  });

  // inventario
  $('notifBtn').addEventListener('click', openNotifications);
  $('invBtn').addEventListener('click', openInventory);
  $('invSearch').addEventListener('input', () => {
    clearTimeout(invTimer);
    invTimer = setTimeout(invSearch, 160);
  });
  $('invSearch').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(invTimer); invSearch(); }
  });
  $('invResults').addEventListener('click', (e) => {
    const edit = e.target.closest('[data-invedit]');
    if (edit) {
      startInvEdit(Number(edit.dataset.invedit));
      return;
    }
    const row = e.target.closest('[data-invidx]');
    if (!row) return;
    selectInvProduct(S.invResults[Number(row.dataset.invidx)]);
  });
  $('invNewBtn').addEventListener('click', () => {
    S.invResults = [];
    S.invSelected = -1;
    $('invResults').innerHTML = '';
    renderInvDetail(null);
    $('invMoves').innerHTML = '';
  });

  // unidades
  $('unitsBtn').addEventListener('click', openUnitsManager);
  $('unitAddBtn').addEventListener('click', addUnit);
  $('unitsList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-unitoff]');
    const on = e.target.closest('[data-uniton]');
    if (off) toggleUnit(Number(off.dataset.unitoff), false);
    else if (on) toggleUnit(Number(on.dataset.uniton), true);
  });
  ['unitName', 'unitSymbol'].forEach((id) => {
    $(id).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); addUnit(); }
    });
  });

  // categorías y ubicaciones
  $('categoryAddBtn').addEventListener('click', addCategory);
  $('categoriesList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-catoff]');
    const on = e.target.closest('[data-caton]');
    if (off) toggleCategory(Number(off.dataset.catoff), false);
    else if (on) toggleCategory(Number(on.dataset.caton), true);
  });
  $('categoryName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addCategory(); }
  });
  $('locationAddBtn').addEventListener('click', addLocation);
  $('locationsList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-locoff]');
    const on = e.target.closest('[data-locon]');
    if (off) toggleLocation(Number(off.dataset.locoff), false);
    else if (on) toggleLocation(Number(on.dataset.locon), true);
  });
  $('locationName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addLocation(); }
  });
  $('reasonAddBtn').addEventListener('click', addReason);
  $('reasonsList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-reasonoff]');
    const on = e.target.closest('[data-reasonon]');
    if (off) toggleReason(Number(off.dataset.reasonoff), false);
    else if (on) toggleReason(Number(on.dataset.reasonon), true);
  });
  $('reasonName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addReason(); }
  });

  // usuarios
  $('usersBtn').addEventListener('click', openUsers);  $('createUserBtn').addEventListener('click', createUser);
  $('usersList').addEventListener('click', (e) => {
    const pinBtn = e.target.closest('[data-userpin]');
    const toggleBtn = e.target.closest('[data-usertoggle]');
    if (pinBtn) openPinModal(Number(pinBtn.dataset.userpin), pinBtn.dataset.username);
    else if (toggleBtn) toggleUser(Number(toggleBtn.dataset.usertoggle),
      toggleBtn.dataset.active === '1', toggleBtn.dataset.username);
  });
  $('pinModalSave').addEventListener('click', savePinModal);
  document.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeModal(btn.dataset.close));
  });

  // cancelación
  $('cancelConfirm').addEventListener('click', confirmCancel);
  $('cancelReason').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (S.security.manager_required_for_cancel) $('cancelPin').focus();
      else confirmCancel();
    }
  });
  $('cancelPin').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmCancel(); }
  });

  // corte
  $('countedCash').addEventListener('input', updateShiftDiff);
  $('confirmCloseShift').addEventListener('click', confirmShiftClose);
  $('countedCash').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmShiftClose(); }
  });
  // contador de denominaciones
  if ($('denomNextBtn')) $('denomNextBtn').addEventListener('click', denomNext);
  if ($('denomBackBtn')) $('denomBackBtn').addEventListener('click', denomBack);
  if ($('denomPrintBtn')) $('denomPrintBtn').addEventListener('click', denomPrint);

  // entradas y salidas de efectivo
  document.querySelectorAll('#cashModal [data-cashkind]').forEach((btn) => {
    btn.addEventListener('click', () => {
      setCashMoveKind(btn.dataset.cashkind);
      $('cashMoveAmount').focus();
    });
  });
  if ($('cashReasonQuick')) {
    $('cashReasonQuick').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-reason]');
      if (!btn) return;
      $('cashMoveReason').value = btn.dataset.reason;
      $('cashMoveReason').focus();
    });
  }
  if ($('cashMoveAmount')) {
    $('cashMoveAmount').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); $('cashMoveReason').focus(); }
    });
  }
  if ($('cashMoveReason')) {
    $('cashMoveReason').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); saveCashMovement(); }
    });
  }
  if ($('cashMoveSave')) {
    $('cashMoveSave').addEventListener('click', saveCashMovement);
  }
  if ($('cashMoveCancelEdit')) {
    $('cashMoveCancelEdit').addEventListener('click', () => {
      resetCashMoveForm();
      $('cashMoveAmount').focus();
    });
  }
  if ($('cashMovesBody')) {
    $('cashMovesBody').addEventListener('click', (e) => {
      const editBtn = e.target.closest('[data-cashedit]');
      const delBtn = e.target.closest('[data-cashdel]');
      if (editBtn) startEditCashMovement(Number(editBtn.dataset.cashedit));
      else if (delBtn) removeCashMovement(Number(delBtn.dataset.cashdel));
    });
  }
  if ($('cashMoveMenuBtn')) {
    $('cashMoveMenuBtn').addEventListener('click', () => {
      closeModal('menuModal');
      openCashMovements();
    });
  }

  // menú
  $('menuBtn').addEventListener('click', () => {
    $('menuStatus').textContent = '';
    $('pinChange').hidden = true;
    $('reprintBtn').hidden = !S.lastSale;
    openModal('menuModal');
  });
  $('customersBtn').addEventListener('click', () => openCustomers('browse'));
  $('testPrintBtn').addEventListener('click', testPrint);
  if ($('printerChip')) $('printerChip').addEventListener('click', openPrinterModal);
  if ($('localPrinterSaveBtn')) $('localPrinterSaveBtn').addEventListener('click', saveLocalPrinter);
  if ($('localPrinterTestBtn')) $('localPrinterTestBtn').addEventListener('click', async () => {
    await saveLocalPrinter();
    await testPrint();
  });
  $('backupBtn').addEventListener('click', backupNow);
  $('changePinBtn').addEventListener('click', () => {
    $('pinChange').hidden = !$('pinChange').hidden;
    if (!$('pinChange').hidden) $('currentPin').focus();
  });
  $('savePinBtn').addEventListener('click', savePin);
  $('reprintBtn').addEventListener('click', () => {
    if (S.lastSale) { reprintSale(S.lastSale.id, S.lastSale.ticket_token); closeModal('menuModal'); }
  });
  $('logoutBtn').addEventListener('click', () => {
    if (confirm('¿Cerrar la sesión de caja?')) logout();
  });

  // teclado global
  document.addEventListener('keydown', (e) => {
    const loginVisible = !$('loginScreen').hidden;
    if (loginVisible) {
      if (e.key === 'Enter') { e.preventDefault(); if ($('bootstrapPanel').hidden) submitPin(); }
      else if (/^[0-9]$/.test(e.key) && $('bootstrapPanel').hidden
        && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        setPin(S.pin + e.key);
      } else if (e.key === 'Backspace' && $('bootstrapPanel').hidden
        && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        setPin(S.pin.slice(0, -1));
      }
      return;
    }

    const modal = visibleModal();
    if (e.key === 'Escape') {
      if (modal) { e.preventDefault(); closeModal(modal); }
      else if (!$('shiftScreen').hidden) { /* no cerrar */ }
      else if (document.activeElement === $('scan')) {
        $('scan').value = '';
        S.search = null;
        renderResultsIdle();
      }
      return;
    }
    if (!$('posMain').hidden) {
      if (e.key === 'F2') { e.preventDefault(); focusScan(); }
      else if (e.key === 'F4' && !modal) { e.preventDefault(); openPayment(); }
      else if (e.key === 'F6' && !modal && isCashRegister()) { e.preventDefault(); openPendingSales(); }
      else if (e.key === 'F7' && !modal) { e.preventDefault(); openCashMovements(); }
      else if (e.key === 'F8' && !modal) { e.preventDefault(); clearCart(false); }
      else if (e.key === 'F9' && !modal) { e.preventDefault(); openSales(); }
      else if (e.key === 'F10' && !modal) { e.preventDefault(); openInventory(); }
      else if (e.key.length === 1 && !modal && document.activeElement !== $('scan')
        && document.activeElement.tagName !== 'INPUT') {
        $('scan').focus();
      }
    }
  });

  window.addEventListener('focus', () => {
    const active = document.activeElement;
    if (active && active.classList && active.classList.contains('qty-input')) return;
    focusScan();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshQuiet(); });
  document.addEventListener('click', (e) => {
    if (e.target.closest('.overlay') || $('posMain').hidden) return;
    if (e.target.matches('input, select, textarea')) return;
    focusScan();
  });
}

/* ------------------------------------------------------------ arranque */
async function init() {
  installNumberWheelLock(); // la rueda no altera campos numéricos
  wire();
  tickClock();
  setInterval(tickClock, 1000);
  setInterval(refreshQuiet, 6000);
  setInterval(() => pingImpresoraLocal(), 5000);   // impresora de ESTA PC
  renderCart();

  try {
    S.boot = await api('/api/pos/info');
  } catch (err) {
    $('loginError').textContent = err.message;
    setInterval(() => location.reload(), 15000);
    return;
  }
  S.version = S.boot.version;
  if (!S.boot.configured) { showBootstrap(); return; }
  pingImpresoraLocal(true);
  if (!S.token) { showLogin(); return; }
  await refresh();
}

document.addEventListener('DOMContentLoaded', init);
