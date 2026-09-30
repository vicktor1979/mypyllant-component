# v0.9.19.15 – magyar opciófeliratok és fordítások javítása

Alap: v0.9.19.14, upstream: v0.9.19. A negyedik verziótag a saját fork javításait jelöli.

## Javítva

- A hiányos magyar nyelvi fájl kiegészítése: a korábbi egy opciófelirat helyett
  az Opciók ablak mind a 18 tényleges mezőjéhez magyar felirat tartozik.
- A `fetch_energy_history` kulcshoz mindkét runtime nyelvi fájlban emberi felirat
  és magyarázat található; a belső kulcs és a mentett érték változatlan.
- A belépési, újrahitelesítési és újrakonfigurálási űrlapok feliratainak,
  címének és hibaüzeneteinek magyarítása.
- A kódban használt, korábban hiányzó `unknown_entry` és
  `reconfiguration_successful` üzenetek hozzáadása angolul és magyarul.
- A meglévő klíma-preset fordítási kulcsok magyar szövegeinek hozzáadása.
- Az angol opciófeliratok rövidítése és egyértelmű mértékegységekkel ellátása.
- Magyar és angol magyarázat az energiaelőzmények letiltásához és az
  energiafrissítési időköz feltételes érvényességéhez.
- Az eredeti `strings.json` katalógus egyeztetése a runtime angol nyelvi fájllal;
  a custom integration futása a `translations/en.json` és `hu.json` tartalmát használja.

## Változatlan

Az integráció egyetlen Python-fájlja sem módosult. Nem változott a frissítési
ütemezés, a sorosítás nélküli működés, az energiaelőzmények kikapcsolhatósága,
a kvótakezelés, a kézi újrapróbálkozás, az entity ID vagy bármely mentett opció.
A dashboard-kártyákhoz nem szükséges módosítás.

## Ellenőrzés

11 új, tisztán helyi fordításellenőrzés és a korábbi 36 izolált működési teszt
sikeres. Az összes runtime Python-fájlt az előző verzióval bájtonként
összehasonlítottam; nincs eltérés. Nem történt élő HA frontend- vagy Vaillant-teszt.
A teljes projekt `prek` ellenőrzése a helyben nem elérhető Python >=3.14.2
követelmény miatt nem futott le.

## Frissítés

A .14-re másolandó HA-fájlok: `manifest.json`, `translations/en.json`,
`translations/hu.json`. Ezután Core-újraindítás, a böngésző teljes frissítése,
és a magyar felületi nyelv ellenőrzése szükséges. A részletes útmutató a
`README-v0.9.19.15-telepites-HU.md` fájlban található.
