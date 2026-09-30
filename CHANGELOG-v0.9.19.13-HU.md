# v0.9.19.13 – energiaelőzmények kikapcsolása és közös frissítési sor

Alap: a beszélgetésben kiadott v0.9.19.12; upstream alap: signalkraft v0.9.19.
Az integráció domainje és a meglévő entitások egyedi azonosítói nem változtak.
A frontend-kártyát nem kell frissíteni.

## Új: Energiaelőzmények lekérése

Az adott myVAILLANT fiók Opciók ablakában új jelölőnégyzet jelenik meg:

**Energiaelőzmények lekérése (induláskor és újratöltéskor is)**

Belső kulcs: `fetch_energy_history`. Alapérték: **false / kikapcsolva**.
Ez szándékos változás: azoknál a meglévő fiókoknál is kikapcsolt lesz, ahol
még nem mentették ezt az új opciót. A korábbi frissítési időközöket nem írja át.

Kikapcsolva:

- nincs induláskori energiaelőzmény-lekérés;
- nincs energiaelőzmény-lekérés kézi vagy automatikus integráció-újratöltéskor;
- nincs periodikus DailyDataCoordinator energiafrissítés;
- a védett DailyDataCoordinator közvetlen frissítése is API-hívás nélkül tér vissza;
- az energiaadatot kérő export, jelentés és tesztadat-szolgáltatás is jelzi, hogy
  előbb engedélyezni kell az opciót; a már ismert energia-kvótát ezek sem kerülik meg.

Az élő rendszeradatok, a kazánvezérlés, a Gateway Online és az API-diagnosztika
megmarad. A `/currentSystem` továbbra is lekérhető: az a rendszer/eszközmodell
felépítéséhez kell, nem azonos a `/devices/.../buckets` fogyasztási idősorával.
A korábban létrehozott energiaentitások és a rögzített statisztikák nem törlődnek;
az energiaentitások kikapcsolt lekérés mellett nem kapnak új adatot, és korábbi
registry-bejegyzéseik „nem biztosított” / nem elérhető állapotot mutathatnak.

Az új jelölőnégyzet módosítása automatikusan újratölti **csak az adott fiókot**.
A többi régi opció alkalmazási módja ebben a kiadásban nem változott.

## Új: egy közös, soros API-frissítési sor

A Home Assistant-példány mypyllant fiókjai közös aszinkron záron osztoznak.
Egyszerre egy frissítési csomag futhat. A csomagok között **20 másodperc** szünet van.
Az indulási csomag együtt tartalmazza a bejelentkezést és a kezdeti lekéréseket.
A beágyazott coordinator-hívások ugyanabban a feladatban nem foglalják le újra a zárat.

A sor kiterjed:

- az integráció indulására és újratöltésére, így a meglévő kézi 🔄 gombra is;
- az időzített élőadat-frissítésre és a coordinator kézzel kért frissítésére;
- az energiaadat-frissítésre, ha azt a felhasználó bekapcsolja;
- a konfiguráció/bejelentkezési adatok ellenőrzésére.

Ez nem HTTP-kérésenkénti 20 másodperc: egy csomagon belül az eredeti könyvtár
sorrendben végzi a szükséges lekéréseket. A vezérlési írásokat és a külön
export-/jelentésszolgáltatásokat nem ez a frissítési sor ütemezi. Más HA-példányt,
a hivatalos alkalmazást és a Vaillant szervert sem tudja koordinálni.

A 20 másodperc helyi, óvatos kezdőérték, **nem közzétett Vaillant-kvóta**.
Hét egyszerre induló fióknál a hat szünet önmagában legalább 120 másodperc;
ehhez jön a lekérések ideje. Várakozás közben nincs Vaillant-hívás.
Egy lassú frissítés késleltetheti az utána sorban álló fiókokat.

A sor túléli az egyes integráció-újratöltéseket. Lemondott várakozó nem foglalja
le tartósan a sort. Kvótát és megváltozott konfigurációs példányt várakozás után
is újra ellenőriz a kód.

## Javítva: az energia-kvóta ne legyen automatikusan élőadat-korlát

A korábbi közös tartós kvótatároló a `/buckets` 403-as hibáját is az egész fiókra
alkalmazta. Ez most külön energia-kvótatárolóba kerül. A többnapos szerveroldali
határidőt **nem törli és nem rövidíti le**.

Az élőadat-lekérés ettől külön megpróbálható. Ha a Vaillant ténylegesen arra is
403/429-et ad, annak account-szintű várakozása változatlanul érvényesül.
A végpontonkénti szétválasztás nem állítja, hogy a szerver minden kvótája végpontszintű.
A 429-et és az ismeretlen végpontos 403-at nem minősíti át energia-korláttá.

Frissítéskor a korábban mentett, egyértelműen `/emf/vN/.../devices/.../buckets`
végpontról származó 403-as kvótát automatikusan áthelyezi az energia-tárolóba,
**előbb elmentve az eredeti határidőt**, és csak utána törölve a téves közös besorolást.
A `/homes`, `/currentSystem` vagy ismeretlen végpont tárolt korlátja érintetlen marad.
Nem kell kézzel szerkeszteni a `.storage` fájlokat.

Bekapcsolt energiaelőzmények esetén az energia-kvóta lejártakor az energia-coordinator
kér új frissítést, nem a teljes fiók. A később sikeresen letöltött energiaadatokhoz
szükséges érzékelők utólag is hozzáadhatók; nem kell csak emiatt újratölteni a kazánokat.

## Időformátum

A válasz törzsében lévő `[nap.]óra:perc:másodperc` forma is feldolgozható.
Példa: `4.15:58:14` = 4 nap 15 óra 58 perc 14 másodperc = 403094 másodperc.
A `Retry-After` fejléc továbbra is elsőbbséget élvez. A határidő nem garancia
arra, hogy a következő próba sikeres lesz: a szerver adhat újabb korlátozást.

## Új magyar diagnosztikai attribútumok

A meglévő `Vaillant API állapot` érzékelő változatlan egyedi azonosítóval megmarad.
A korábbi attribútumok mellett megjelenik:

- `Energiaelőzmények lekérése`
- `Energiaelőzmények API-korlátja`
- `Energiaelőzmények újrapróbálkozása`
- `Energiaelőzmények hibája`
- `API-frissítés sorban áll`
- `API-frissítés folyamatban`

Ezek a meglévő helyi, 60 másodperces diagnosztikai frissítéssel is frissülnek;
nem indítanak új API-hívást. A sorba állási jelzés ezért nem másodperc-pontosságú.
Az API-állapot lehet `Kapcsolódva` egy megőrzött energia-kvóta mellett, ha az élő
rendszeradatok lekérése sikeres volt. A meglévő táblázatos frontend ehhez nem igényel módosítást.

## Ellenőrzés

- 38 izolált regressziós teszt sikeres, valódi asyncio-végrehajtással és HA/API
  helyettesítő objektumokkal: `python scripts/check_v013_offline.py`.
- Ezek többek között lefuttatják a tényleges setup/coordinator kódot, hét egyidejű
  fiókfrissítést, a beágyazott frissítést, lemondásokat, kvótamigrációt, újraindítás
  szimulációját, a kikapcsolt energiaútvonalakat és a napos időformátumot.
- Python-szintaktika, JSON és TOML ellenőrizve.
- Külön Home Assistant regressziós tesztek: `tests/test_energy_history.py`.
- A teljes Home Assistant pytest/pre-commit futás itt nem történt meg. A projekt
  Python >=3.14.2-t igényel; a környezetben 3.13.5 van. Az uv Python-letöltése
  DNS-hibával meghiúsult. Az izolált tesztek nem helyettesítik a teljes HA/CI és
  valódi Vaillant-fiókos próbát. A felhasználó szerverét nem értem el.
