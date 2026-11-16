/* 📚 Ma bibliothèque : les groupes déjà rangés (clips et musique), leur fiche d'images, le remplacement avec sauvegarde.
   Chargé par index.html après son script (utilise $, esc, api, post, vign, etiquette, MouConfirm). Rien n'est modifié avant « Appliquer ». */
(function () {
  'use strict';
  var LISTE = [], CHARGE = false, B = null, CH = null, TS = Date.now(), timerMsg = null;
  var norm = function (s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); };
  var NOMS = {vignette: '🖼️ Poster du groupe', banniere: '🎞️ Bannière', logo: '🔤 Logo'};
  var CSS_TYPE = {vignette: 'carre', banniere: 'banniere', logo: 'logo', fond: 'fond', couverture: 'carre', cdart: 'carre'};
  var LIEUX = {clips: '🎬 dossier des clips', musique: '🎵 dossier de la musique'};
  var COURT = {clips: '🎬 clips', musique: '🎵 musique'};

  function bnote(t, k) { $('biblioMsg').innerHTML = t ? '<div class="note ' + (k || '') + '">' + esc(t) + '</div>' : ''; }
  function fnote(t, k) {
    var m = $('biblioNote'); m.textContent = t; m.className = 'fi-msg ' + (k || ''); m.hidden = !t;
    clearTimeout(timerMsg); if (t) timerMsg = setTimeout(function () { m.hidden = true; }, k === 'err' ? 12000 : 6000);
  }
  function urlFichier(id, d, f) { return '/api/bibliotheque/' + id + '/fichier?d=' + d + '&f=' + encodeURIComponent(f) + '&t=' + TS; }

  // ------------------------------------------------------------------ la liste
  function carte(g) {
    var d = g.poster.clips ? 'clips' : g.poster.musique ? 'musique' : '';
    var img = d ? '<img loading="lazy" alt="" src="' + urlFichier(g.id, d, 'folder.jpg') + '">' : '<div class="bib-sans">pas de poster</div>';
    return '<button type="button" class="bib-carte" data-id="' + g.id + '">' + img + '<b>' + esc(g.nom) + '</b><span class="bib-pills">' +
      (g.clips ? '<i>🎬 clips</i>' : '') + (g.musique ? '<i>🎵 musique</i>' : '') + (g.mbid ? '' : '<i class="a-id">à identifier</i>') + '</span></button>';
  }
  function dessinerListe() {
    var q = norm($('biblioQ').value), l = LISTE.filter(function (g) { return !q || norm(g.nom).indexOf(q) >= 0; });
    $('biblioListe').innerHTML = l.length ? l.map(carte).join('') : '<p class="muted">' + (LISTE.length ? 'Aucun groupe ne correspond.' : 'Aucun groupe trouvé dans les dossiers.') + '</p>';
    $('biblioCompte').textContent = LISTE.length ? l.length + ' groupe' + (l.length > 1 ? 's' : '') + (q ? ' sur ' + LISTE.length : '') : '';
  }
  async function charger() {
    bnote('Lecture des dossiers…');
    var d = await api('/api/bibliotheque');
    if (d.error) return bnote(d.error, 'err');
    LISTE = d.groupes || []; CHARGE = true; TS = Date.now();
    var manque = [!d.racines.clips ? 'des clips' : '', !d.racines.musique ? 'de la musique' : ''].filter(Boolean);
    bnote(manque.length ? 'Le dossier ' + manque.join(' et le dossier ') + ' est introuvable (le partage est-il monté ?).' : '', 'warn');
    dessinerListe();
  }

  // ------------------------------------------------------------------ la fiche
  function tuilesExistantes(typeCss, existant, rel, albumDossiers) {
    var t = '';
    ['clips', 'musique'].forEach(function (s) {
      if (!existant[s]) return;
      var r = albumDossiers ? albumDossiers[s] + '/' + rel : rel;
      t += vign(typeCss + ' actuelle fixe', urlFichier(B.id, s, r), '<span class="actu">✔ Actuelle</span> · ' + COURT[s], false, 'data-fixe="1"');
    });
    return t;
  }
  function tuilesCopie(typeCss, type, cle, existant, rel, albumDossiers, choisi) {
    var t = '';
    ['clips', 'musique'].forEach(function (s) {
      if (!existant[s]) return;
      var r = albumDossiers ? albumDossiers[s] + '/' + rel : rel, url = 'copie:' + s + ':' + r, sel = choisi === url;
      t += vign(typeCss, urlFichier(B.id, s, r), etiquette('copie ' + COURT[s]), sel, 'data-t="' + type + '" ' + cle + ' data-url="' + esc(url) + '"');
    });
    return t;
  }
  function bande(type, cssType, bloc, cleAttr, choisi, albumDossiers, relFichier) {
    var t = tuilesExistantes(cssType, bloc.existant, relFichier, albumDossiers) + tuilesCopie(cssType, type, cleAttr, bloc.existant, relFichier, albumDossiers, choisi);
    (bloc.candidats || []).forEach(function (c) {
      var sel = choisi === c.url, ex = bloc.existant.clips || bloc.existant.musique;
      t += vign(cssType, c.apercu || c.url, etiquette(c.source) + (sel && ex ? ' <span class="remp">♻ remplacera l’actuelle</span>' : ''), sel, 'data-t="' + type + '" ' + cleAttr + ' data-url="' + esc(c.url) + '"');
    });
    if (!(bloc.candidats || []).length && !(bloc.existant.clips || bloc.existant.musique)) t += '<div class="muted" style="padding:10px">Aucune image trouvée : ajoute la tienne ci-dessous.</div>';
    return '<div class="bande">' + t + '</div>';
  }
  function ajout(type, album) {
    var a = album == null ? '' : ' data-album="' + album + '"';
    return '<div class="ajout-img"><button type="button" class="btn small ghost" data-bib-fichier="' + type + '"' + a + '>📁 Ajouter un fichier</button><button type="button" class="btn small ghost" data-bib-lien="' + type + '"' + a + '>🔗 Ajouter une adresse</button></div>';
  }
  function blocFonds() {
    var f = B.fonds, ch = CH.fonds, t = '';
    ['clips', 'musique'].forEach(function (s) {
      f.existants[s].forEach(function (ok, i) {
        if (ok) t += vign('fond actuelle fixe', urlFichier(B.id, s, f.fichiers[i]), '<span class="actu">✔ Actuel</span> · ' + COURT[s] + ' · n° ' + (i + 1), false, 'data-fixe="1"');
      });
    });
    f.candidats.forEach(function (c) {
      var rang = ch.indexOf(c.url);
      t += vign('fond', c.apercu || c.url, etiquette(c.source) + (rang >= 0 ? ' <span class="remp">fond n° ' + (rang + 1) + '</span>' : ''), rang >= 0, 'data-t="fonds" data-url="' + esc(c.url) + '"');
    });
    return '<div class="bande">' + t + '</div>';
  }
  function blocAlbum(a, i) {
    var cle = function (t) { return 'data-album="' + i + '" data-k="' + t + '"'; }, ch = CH.albums[a.nom] || {};
    var fichiers = function (titre, liste) { return liste.length ? '<div class="bib-fichiers"><b>' + titre + ' (' + liste.length + ')</b><ul>' + liste.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul></div>' : ''; };
    var cible = function (t, nom, rel) {
      var bloc = {existant: a[t], candidats: a.candidats[t]};
      return '<div class="aide" style="margin-top:8px"><b>' + nom + '</b> (' + rel + ')</div>' + bande('albums', CSS_TYPE[t], bloc, cle(t), ch[t], a.dossiers, rel) + ajout(t, i);
    };
    return '<details class="bib-album"' + (B.albums.length <= 3 ? ' open' : '') + '><summary>💿 ' + esc(a.nom) + ' <span class="muted">· ' + a.clips.length + ' clip' + (a.clips.length > 1 ? 's' : '') + ' · ' + a.morceaux.length + ' morceau' + (a.morceaux.length > 1 ? 'x' : '') + '</span></summary>' +
      fichiers('🎬 Clips', a.clips) + fichiers('🎵 Morceaux', a.morceaux) + cible('couverture', 'Pochette', 'folder.jpg') + cible('cdart', 'CD (cdart)', 'cdart.png') + '</details>';
  }
  function blocIdentifier() {
    return '<section class="fi"><h3>❓ Quel groupe est-ce ?</h3><div class="aide">Ce dossier n’a pas de fiche (artist.nfo) : dis de quel groupe il s’agit pour voir les images proposées. C’est retenu pour la prochaine fois.</div>' +
      '<div class="recherche"><input type="text" id="biblioQuel" value="' + esc(B.nom.replace(/\s*[(\[].*$/, '')) + '" autocomplete="off" autocapitalize="off" spellcheck="false"><button class="btn" id="biblioQuelGo">Chercher</button></div><div id="biblioQuelRes"></div></section>';
  }
  function compter() {
    var n = Object.keys(CH.images).length + CH.fonds.length;
    Object.keys(CH.albums).forEach(function (a) { n += Object.keys(CH.albums[a]).length; });
    return n;
  }
  function majBarre() {
    var n = compter(), lieux = ['clips', 'musique'].filter(function (s) { return CH.cibles[s]; }).map(function (s) { return LIEUX[s]; });
    $('biblioResume').textContent = n ? n + ' image' + (n > 1 ? 's' : '') + ' choisie' + (n > 1 ? 's' : '') + ' → ' + (lieux.join(' + ') || 'aucun dossier coché') : 'Touche une image pour la choisir : rien ne change tant que tu n’appuies pas sur « Appliquer ».';
    $('biblioAppliquer').disabled = !n || !lieux.length;
  }
  function dessinerFiche() {
    var h = '<div class="bib-haut"><h2>' + esc(B.nom) + '</h2><div class="muted">' + (B.mbid ? 'Identifié (MusicBrainz) · ' : '') + (B.dossiers.clips ? '🎬 clips ' : '') + (B.dossiers.musique ? '🎵 musique' : '') + '</div></div>';
    h += '<div class="fi-corps">';
    if (B.avertissement) h += '<div class="note warn">' + esc(B.avertissement) + '</div>';
    if (!B.mbid) h += blocIdentifier();
    h += '<section class="fi"><h3>📂 Où appliquer les images</h3><div class="aide">Les images choisies remplacent celles du ou des dossiers cochés. Chaque image remplacée est d’abord sauvegardée.</div>';
    ['clips', 'musique'].forEach(function (s) {
      h += B.dossiers[s] ? '<label class="qual"><input type="checkbox" data-cible="' + s + '"' + (CH.cibles[s] ? ' checked' : '') + '><span>' + LIEUX[s] + '<br><small class="muted">' + esc(B.dossiers[s]) + '</small></span></label>'
                         : '<div class="muted" style="padding:6px 0">' + LIEUX[s] + ' : ce groupe n’y est pas.</div>';
    });
    h += '</section>';
    ['vignette', 'banniere', 'logo'].forEach(function (t) {
      var b = B.images[t];
      h += '<section class="fi"><h3>' + NOMS[t] + ' <span class="muted">· ' + esc(b.fichier) + '</span></h3>' + bande('images', CSS_TYPE[t], b, 'data-k="' + t + '"', CH.images[t], null, b.fichier) + ajout(t) + '</section>';
    });
    h += '<section class="fi"><h3>🌄 Fonds d’écran</h3><div class="aide">Choisis jusqu’à 4 fonds (touche-les dans l’ordre voulu : le 1er devient fanart.jpg, les suivants le dossier extrafanart). Ils remplacent les fonds actuels.</div>' + blocFonds() + ajout('fanart') + '</section>';
    h += '<section class="fi"><h3>💿 Albums, clips et morceaux</h3><div class="aide">Pochette et cdart de chaque album. Seuls les albums déjà présents sont modifiés : aucun dossier n’est créé.</div>' + (B.albums.length ? B.albums.map(blocAlbum).join('') : '<div class="muted">Aucun album.</div>') + '</section>';
    h += '</div>';
    $('biblioCorps').innerHTML = h; majBarre();
  }
  function redessiner() { var y = $('biblio').scrollTop; dessinerFiche(); $('biblio').scrollTop = y; }
  async function ouvrir(id) {
    bnote('Ouverture de la fiche (la première fois, les images sont cherchées sur Internet : quelques secondes)…');
    var d = await api('/api/bibliotheque/' + id);
    if (d.error) return bnote(d.error, 'err');
    bnote(''); B = d; CH = {images: {}, fonds: [], albums: {}, cibles: {clips: !!d.dossiers.clips, musique: !!d.dossiers.musique}};
    dessinerFiche(); $('biblio').classList.add('ouvert'); $('biblio').scrollTop = 0; document.body.style.overflow = 'hidden';
  }
  function fermer() { $('biblio').classList.remove('ouvert'); document.body.style.overflow = ''; B = null; CH = null; if (CHARGE) charger(); }

  // ------------------------------------------------------------------ les choix
  function basculer(e) {
    var t = e.dataset.t, url = e.dataset.url;
    if (t === 'images') { var k = e.dataset.k; if (CH.images[k] === url) delete CH.images[k]; else CH.images[k] = url; }
    else if (t === 'fonds') {
      var i = CH.fonds.indexOf(url);
      if (i >= 0) CH.fonds.splice(i, 1); else if (CH.fonds.length >= 4) return fnote('4 fonds au plus : retire-en un d’abord.', 'warn'); else CH.fonds.push(url);
    } else if (t === 'albums') {
      var a = B.albums[+e.dataset.album].nom, c = CH.albums[a] || (CH.albums[a] = {});
      if (c[e.dataset.k] === url) delete c[e.dataset.k]; else c[e.dataset.k] = url;
      if (!Object.keys(c).length) delete CH.albums[a];
    }
    redessiner();
  }
  async function envoyer(type, album, fichier, lien) {
    if (!B.mbid) return fnote('Identifie d’abord le groupe : tes images sont rangées sous son identifiant.', 'warn');
    fnote('Envoi de l’image…', '');
    var fd = new FormData(); fd.append('type', type);
    if (album != null) fd.append('album', B.albums[+album].nom);
    if (fichier) fd.append('fichier', fichier); if (lien) fd.append('lien', lien);
    var r, d;
    try { r = await fetch('/api/bibliotheque/' + B.id + '/image', {method: 'POST', body: fd}); d = await r.json(); }
    catch (x) { d = {error: 'Envoi impossible' + (r ? ' (code ' + r.status + ')' : '') + '.'}; }
    if ((r && !r.ok) || d.error) return fnote(d.error || 'Ajout impossible.', 'err');
    B = d; redessiner(); fnote('Image ajoutée : elle est proposée dans la rangée (étiquette « toi »). Touche-la pour la choisir.', '');
  }
  async function appliquer() {
    var n = compter(), cibles = ['clips', 'musique'].filter(function (s) { return CH.cibles[s]; });
    if (!n || !cibles.length) return;
    var ok = await MouConfirm(n + ' image' + (n > 1 ? 's' : '') + ' vont être posées dans : ' + cibles.map(function (s) { return LIEUX[s]; }).join(' et ') + '.\nChaque image remplacée est sauvegardée d’abord. Continuer ?', {ok: 'Appliquer', annuler: 'Annuler'});
    if (!ok) return;
    $('biblioAppliquer').disabled = true; fnote('Application des images…', '');
    var fonds = CH.fonds.slice();
    var d = await post('/api/bibliotheque/' + B.id + '/appliquer', {cibles: cibles, images: CH.images, fonds: fonds, albums: CH.albums});
    var txt = (d.faits || []).length ? (d.faits.length + ' image' + (d.faits.length > 1 ? 's' : '') + ' posée' + (d.faits.length > 1 ? 's' : '') + '. ') : '';
    if (d.ignores && d.ignores.length) txt += d.ignores.join(' ; ') + '. ';
    if (d.erreurs && d.erreurs.length) txt += 'Problèmes : ' + d.erreurs.join(' ; ') + '. ';
    if (d.emby) txt += d.emby;
    fnote(txt || d.error || 'Rien n’a été modifié.', d.error && !(d.faits || []).length ? 'err' : (d.erreurs && d.erreurs.length ? 'warn' : ''));
    TS = Date.now();
    var f = await api('/api/bibliotheque/' + B.id);
    if (!f.error) { B = f; CH.images = {}; CH.fonds = []; CH.albums = {}; redessiner(); }
    $('biblioAppliquer').disabled = false;
  }
  async function chercherGroupe() {
    var q = $('biblioQuel').value.trim(); if (q.length < 2) return;
    $('biblioQuelRes').innerHTML = '<div class="muted">Recherche…</div>';
    var d = await api('/api/groupes/chercher?q=' + encodeURIComponent(q));
    if (d.error) return ($('biblioQuelRes').innerHTML = '<div class="note err">' + esc(d.error) + '</div>');
    var g = (d.groupes || []).slice(0, 8);
    $('biblioQuelRes').innerHTML = g.length ? g.map(function (x) { return '<div class="cand" data-quel="' + esc(x.mbid) + '"><b>' + esc(x.nom) + ' <small>' + esc([x.type, x.pays, x.debut, x.precision].filter(Boolean).join(' · ')) + '</small></b></div>'; }).join('')
                                            : '<div class="note warn">Aucun groupe trouvé. Colle l’adresse de sa fiche MusicBrainz dans la case.</div>';
  }

  // ------------------------------------------------------------------ branchements
  $('biblioPanel').addEventListener('toggle', function () { if ($('biblioPanel').open && !CHARGE) charger(); });
  $('biblioQ').addEventListener('input', dessinerListe);
  $('biblioActualiser').addEventListener('click', charger);
  $('biblioListe').addEventListener('click', function (e) { var c = e.target.closest('.bib-carte'); if (c) ouvrir(c.dataset.id); });
  $('biblioFermer').addEventListener('click', fermer);
  $('biblioAppliquer').addEventListener('click', appliquer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && $('biblio').classList.contains('ouvert') && !$('fiche').classList.contains('ouvert')) fermer(); });
  $('biblioFichierImg').addEventListener('change', function () {
    var f = $('biblioFichierImg').files[0], t = $('biblioFichierImg').dataset.type, a = $('biblioFichierImg').dataset.album;
    $('biblioFichierImg').value = ''; if (f && B) envoyer(t, a === '' ? null : a, f, null);
  });
  $('biblioCorps').addEventListener('click', async function (e) {
    var v = e.target.closest('button.vign');
    if (v && v.dataset.t && !v.dataset.fixe) return basculer(v);
    var bf = e.target.closest('[data-bib-fichier]'), bl = e.target.closest('[data-bib-lien]');
    if (bf) { var inp = $('biblioFichierImg'); inp.dataset.type = bf.dataset.bibFichier; inp.dataset.album = bf.dataset.album == null ? '' : bf.dataset.album; inp.click(); return; }
    if (bl) {
      var lien = await (window.MouPrompt ? MouPrompt('Adresse de l’image (https://…)', '', {}) : Promise.resolve(window.prompt('Adresse de l’image (https://…)')));
      if (lien && lien.trim()) envoyer(bl.dataset.bibLien, bl.dataset.album == null ? null : bl.dataset.album, null, lien.trim());
      return;
    }
    var cb = e.target.closest('input[data-cible]');
    if (cb) { CH.cibles[cb.dataset.cible] = cb.checked; majBarre(); return; }
    if (e.target.closest('#biblioQuelGo')) return chercherGroupe();
    var q = e.target.closest('[data-quel]');
    if (q) {
      fnote('Identification…', '');
      var d = await post('/api/bibliotheque/' + B.id + '/identifier', {mbid: q.dataset.quel});
      if (d.error) return fnote(d.error, 'err');
      if (d.id) { B = d; CH = {images: {}, fonds: [], albums: {}, cibles: {clips: !!d.dossiers.clips, musique: !!d.dossiers.musique}}; redessiner(); fnote('Groupe identifié : les images proposées sont chargées.', ''); }
    }
  });
  $('biblioCorps').addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target.id === 'biblioQuel') chercherGroupe(); });
})();
