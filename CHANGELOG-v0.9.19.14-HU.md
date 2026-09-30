# v0.9.19.14 – .12-alapú független fiókfrissítések, energiaopcióval

Upstream: signalkraft v0.9.19. A saját .12 működéséből megmarad a fiókonkénti
frissítés; a .13 közös sorosítása visszavonva. A .13 többi energiafunkciója megmarad.

## Eltávolítva

- Közös `ApiRefreshQueue`, megosztott aszinkron zár és 20 másodperces szünet.
- Sorba állítás induláskor, újratöltéskor, élő-/energiaadat-frissítéskor és
  bejelentkezési adatok ellenőrzésekor.
- Sorba állási/aktív közös frissítési attribútumok az API-státuszból.
- Az `api_queue.py` modult az új kód nem importálja; a forrásfából törölve.

## Megtartva a .13-ból

- `fetch_energy_history` jelölőnégyzet; alapértéke false.
- Energiaelőzmények kihagyása induláskor, újratöltéskor, periodikus és közvetlen
  frissítéskor; a külön energiaexport/jelentés is tiszteletben tartja az opciót.
- Külön energia-kvótatárolás, régi `/buckets` 403 korlátok átvezetése és eredeti
  határidejük megőrzése. A `/homes` és egyéb fiókszintű korlátok nem törlődnek.
- Az energia-kvóta lejárata csak az energiafrissítőt indítja újra; nem kell miatta
  teljes fiókot újratölteni. Energiaérzékelők később is létrejöhetnek.
- Magyar energiaattribútumok, napot is tartalmazó várakozási idő feldolgozása.
- Az új energiaopció elmentése az adott fiókot automatikusan újratölti.

## Változatlan a .12-höz képest

- Fiókok frissítési időközei, élő vezérlés, gateway-hibák rendszerenkénti izolálása.
- Magyar API-diagnosztika, kézi újrapróbálkozás gomb, entitások egyedi azonosítói.
- Kvótalejárat utáni, korábban is meglévő 2 másodperces biztonsági ráhagyás és
  0–14 másodperces fiókonkénti eltolás; ez nem közös sor vagy globális várakozás.

## Lényeges implementációs részlet

A .13 dekorátorában lévő energia-tiltó ellenőrzés a dekorátor eltávolításával
nem veszhetett el: közvetlenül a DailyDataCoordinator frissítőmetódusának
**elejére** került, a tokenfrissítés és a kvótaellenőrzések elé.
Így kikapcsolt energiaelőzmények mellett a közvetlen frissítés is helyben tér vissza.

## Telepítés és teszt

A 11 fájlos HA-csomag a .12-re vagy .13-ra közvetlenül rátehető. Teljes Core-restart
szükséges. A .13 `api_queue.py` fájlja törölhető; ottmaradva sincs használatban.
Nem kell fiókokat/kazánokat újra felvenni vagy frontend-kártyát cserélni.

36 izolált asyncio/forráskód teszt sikeres; Python-, JSON- és TOML-ellenőrzések
sikeresek. Teljes HA/prek/pytest nem futott le a szükséges Python-letöltés
DNS-hibája miatt. Élő Vaillant-fiókos teszt nem történt.
