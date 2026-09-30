# mypyllant v0.9.19.15 – magyar feliratok javítása

Alap: a beszélgetésben kiadott **v0.9.19.14**, upstream alap: signalkraft v0.9.19.
Ez kizárólag fordítási és verziójelölési javítás. A futó integráció Python-kódja
bájtról bájtra azonos a .14 változattal.

## Mi volt hibás?

A .14 HA-csomag tényleges `translations/hu.json` fájljában az opciók közül
csak a `fetch_energy_history` felirat és annak leírása szerepelt magyarul.
A további 17, az OPTIONS_SCHEMA-ban szereplő mező magyar felirata hiányzott.
Az angol fájlban ezek megvoltak, így magyar felületen is angol tartalékfordítás
jelenhetett meg.

Az új energiajelölőnégyzet fordítása a .14 ZIP-ben mindkét nyelven benne volt.
A képernyőképen látható nyers `fetch_energy_history` felirat ezért arra utal,
hogy a telepítésben vagy a fordítási gyorsítótárban nem ez az új tartalom volt
használatban. A képernyőkép alapján nem dönthető el, hogy kimaradt-e a
`translations` almappa másolása, vagy gyorsítótár okozta a jelenséget.

A .15 a teljes magyar nyelvi fájlt tartalmazza. A magyar szöveg a megjelenítésben
változik: a belső `fetch_energy_history` kulcsot nem nevezzük át.

## Telepítés a .14 fölé

1. Mentsd le a jelenlegi integrációt a `custom_components` könyvtáron kívülre.
2. A HA ZIP **mindhárom** fájlját másold a megfelelő helyére:

   ```text
   /config/custom_components/mypyllant/manifest.json
   /config/custom_components/mypyllant/translations/en.json
   /config/custom_components/mypyllant/translations/hu.json
   ```

   A két JSON nem közvetlenül a `mypyllant` mappába, hanem annak
   **`translations` almappájába** kerül. A többi nyelvi fájlt és az integráció
   többi fájlját ne töröld. A ZIP nem teljes integráció.

   Proxmox esetén továbbra is a Home Assistant VM konfigurációját kell
   módosítani, nem csak a Proxmox hostra felmásolni a fájlokat.

3. Zárd be a nyitott Opciók ablakot. Indítsd újra a Home Assistant Core-t:

   ```bash
   ha core restart
   ```

   A teljes Core-újraindítás azért javasolt, mert a backend gyorsítótárazza
   a fordításokat; pusztán az adott fiók újratöltése nem feltétlenül elég.

4. A böngészőben nyomj **Ctrl+F5**-öt, majd nyisd meg újra az Opciókat.
5. A Home Assistant saját felhasználói profiljában a felület nyelve legyen
   **Magyar**: Profil / Általános / Nyelv. Angol felületi nyelvnél az angol
   feliratok szándékosan maradnak angolok; ilyenkor sem szabad nyers belső
   kulcsnak megjelennie.

A fordításért nem kell újra hozzáadni a fiókokat vagy eltávolítani a kazánokat.
A frontend-kártya erőforrásához nem kell hozzányúlni.

## Várható feliratok

- Élő adatok frissítési időköze (másodperc)
- Energiaelőzmények lekérése (induláskor és újratöltéskor is)
- Energiaelőzmények frissítési időköze (másodperc)
- Módosítás utáni frissítés késleltetése (másodperc)
- Ideiglenes felülbírálás alapértelmezett időtartama (óra)
- Távollét alapértelmezett időtartama (nap)
- Távollét alapértelmezett célhőmérséklete (°C)
- Kézi hűtés alapértelmezett időtartama (nap)
- A hőmérséklet-állítás az időprogramot módosítsa ideiglenes felülbírálás helyett
- Legionella-védelem jelzési küszöbe (°C)
- Valós idejű statisztikák lekérése
- Valós idejű teljesítményadatok lekérése
- Rendszerkapcsolat állapotának külön lekérése
- Diagnosztikai hibakódok lekérése
- Az ambiSENSE támogatottságának lekérése
- ambiSENSE szobatermosztátok lekérése
- Energiagazdálkodási adatok lekérése
- EEBUS-adatok lekérése

A belépés, újrahitelesítés és újrakonfigurálás saját mezőfeliratai és üzenetei is
magyar fordítást kaptak. A klíma már meglévő négy `preset_mode` fordítási kulcsához
magyar megjelenítési szöveg került. A tárolt állapotértékek nem változtak.
Ez nem az összes entitásnév vagy szolgáltatás teljes magyarítása; a Pythonban
rögzített, illetve a felhasználó által átnevezett entitásnevekhez nem nyúltunk.
Az országlista könyvtárból származó elemei szintén változatlanok.

## Mi nem változott?

- A mentett frissítési időközök és az opciók belső kulcsai.
- Az energiaelőzmények jelölőnégyzetének működése és kikapcsolt alapértéke.
- A fiókok független frissítése; nem került vissza közös frissítési sor vagy zár.
- Az energia- és élőadat-kvóta külön kezelése.
- A kézi újrapróbálkozó gombok és az automatikus kvótakezelés.
- Az entitások egyedi azonosítói és a konfigurációk.
- A korábban jelzett opciókezelési sajátosságok: ez a kiadás nem javítja vagy
  változtatja meg a hőmérséklet- és egyéb opciók Pythonbeli alkalmazását.

A pipa nélküli energiaelőzmény-mező továbbra is **kikapcsolt** lekérést jelent.
Nem kell bekapcsolni a fordítás érvényesítéséhez.

## Ellenőrzés

- 11 fordítási ellenőrzés sikeres, a valós `config_flow.py` sémájának AST-vizsgálatával:
  mind a 18 opció, a belépési mezők és a kódban explicit módon használt hibaüzenetek
  rendelkeznek fordítással; a HU és EN kulcskészlet egyezik.
- A tényleges `translations/en.json` és `hu.json` fájlokban nincs feloldatlan
  Home Assistant Core-féle `[%key:...%]` hivatkozás.
- A .14 meglévő 36 izolált működési tesztje változatlanul sikeres a .15 összeállításon.
- A manifest, pyproject és lock verziója egységesen 0.9.19.15.
- A `custom_components/mypyllant` alatti Python-fájlok bájtonkénti összehasonlítása
  nem mutat változást a .14-hez képest.

Futtatás a repository gyökeréből:

```bash
uv run --no-project python scripts/check_translations.py
uv run --no-project python scripts/check_v014_offline.py
```

Az első ellenőrzés csak Python standard library-t használ. A második helyi
Home Assistant/API-helyettesítő objektumokat és az aiohttp környezetét használja.
Nem történt élő Vaillant API-hívás vagy valódi Home Assistant frontend-teszt.
A teljes `uv run prek` folyamat nem indult el: a projekt Python >=3.14.2-t kér,
a helyi interpreter 3.13.5, és az ellenőrzést offline módban próbáltam.
A sikeres izolált tesztek nem helyettesítik a teljes HA/CI ellenőrzést.

## Csomagok

- HA ZIP: a két runtime nyelvi JSON és a manifest, összesen három fájl.
- Fork ZIP: a HA fájlok mellett a meglevő `strings.json` katalógus szinkronizált
  példánya, a verziófájlok, a fordításellenőrző script és ez a dokumentáció/changelog.
  Egyedi integrációnál a tényleges futás a `translations/*.json` fájlokat használja,
  nem a katalógusként megtartott `strings.json` fájlt.

## Külső technikai háttér

- Home Assistant custom integration localization:
  https://developers.home-assistant.io/docs/internationalization/custom_integration/
- Backend localization és fordítási gyorsítótár:
  https://developers.home-assistant.io/docs/internationalization/core/
  https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/translation.py
- Home Assistant felhasználói felület nyelve:
  https://www.home-assistant.io/docs/configuration/user-configuration/
