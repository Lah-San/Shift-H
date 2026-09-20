/* Console boot: one sidebar for managing and for policy. Lands on the inbox whenever approvals are waiting. */
(async () => {
  const s = session.get();
  $$('.sidenav [data-role="admin"]').forEach(el => { el.hidden = s.role !== 'admin'; });
  $('#foot-note').firstChild.textContent = s.role === 'admin' ? 'Policy-backed rules change only with an Australian policy document; system rules with a reason. Every decision and change is logged.' : 'Every recommendation is logged. Declines need written reasons (ANF cl.33).';
  let pending = 0;
  try { pending = (await api('/api/manager/inbox')).length; } catch (e) { }
  $('#n-inbox').textContent = pending; $('.sidenav [data-screen="inbox"]').classList.toggle('urgent', pending > 0);
  try { const u = await api('/api/manager/notifications/unread'); $('#n-notes').textContent = u.unread; $('#n-notes').hidden = !u.unread; } catch (e) { }
  const want = location.hash.slice(1);
  const allowed = want && $(`.sidenav [data-screen="${want}"]`) && !$(`.sidenav [data-screen="${want}"]`).closest('[hidden]');
  go(pending > 0 && !allowed ? 'inbox' : allowed ? want : (s.role === 'admin' ? 'rules' : 'coverage'));
  if (pending > 0 && allowed && want !== 'inbox') toast(`${pending} request${pending > 1 ? 's' : ''} waiting for a decision in the inbox`, 4000);
})();
