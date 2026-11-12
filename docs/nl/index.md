---
layout: default
title: "Imou robotstofzuiger in Home Assistant koppelen (HACS)"
description: "Geen Imou-integratie voor robotstofzuigers? Met Imou Vacuum (Open Platform) stuur je je Imou-robot aan vanuit Home Assistant. Gratis, via HACS."
lang: nl
permalink: /nl/
image: /images/device-page.png
hreflang:
  - lang: en
    url: /
  - lang: nl
    url: /nl/
  - lang: x-default
    url: /
---

# Imou robotstofzuiger koppelen aan Home Assistant

*[English version]({{ "/" | relative_url }})*

Wil je een Imou robotstofzuiger bedienen vanuit Home Assistant? Dat kan met de gratis, open-source integratie "Imou Vacuum (Open Platform)", die je installeert via HACS. De bestaande Imou-integraties ondersteunen geen robotstofzuigers, dus je vond daar waarschijnlijk niets. Ik heb deze integratie gemaakt om dat gat te vullen, en hieronder lees je stap voor stap hoe je hem instelt.

![Apparaatpagina van een Imou RV-L11-A in Home Assistant met de integratie Imou Vacuum (Open Platform)]({{ "/images/device-page.png" | relative_url }})

## Waarom de bestaande Imou-integraties niet werken voor robotstofzuigers

Voor Imou bestaan drie integraties: de ingebouwde Imou-integratie van Home Assistant en de HACS-integraties Imou Life (`imou_life`) en Imou-OpenPlatform. Alle drie ondersteunen camera's en andere apparaten, maar geen robotstofzuigers. Je kunt je robot er dus niet mee bedienen, hoe je de koppeling ook instelt.

De integratie in dit artikel is speciaal voor robotstofzuigers gemaakt. Hij praat met de officiële cloud-API van het Imou Open Platform.

## Wat de integratie kan

Na het instellen krijg je in Home Assistant:

- Een standaard stofzuiger-entiteit met starten, pauzeren, hervatten, stoppen, terug naar het basisstation en lokaliseren. De zuigkracht stel je in als ventilatorsnelheid (bij de L11-A: stil, standaard of max; bij RV1-modellen: stil, standaard, sterk of max).
- Kamerreiniging (zie verderop).
- Sensoren voor batterij, status, foutmelding, schoongemaakt oppervlak, schoonmaaktijd, slijtage van onderdelen (borstels, filter, sensoren), wifi-signaal en opladen (RV1-modellen). De sensoren voor het basisstation staan standaard uit; geen enkel model is geverifieerd met een status van het basisstation.
- Schakelaars, bijvoorbeeld voor extra kracht op tapijt, en keuzelijsten voor zuigkracht en waterniveau (bij RV1-modellen ook waterhoeveelheid).
- Knoppen om de stofbak te legen in het afzuigstation, en een knop **Clean <room>** per kamer.
- Een Schedules-sensor die de schema's uit de Imou-app toont.
- `vacuum.send_command` voor geavanceerd gebruik.

Je stelt alles in via de Home Assistant-interface. De namen van entiteiten zijn Engels.

## Wat je nodig hebt

- Home Assistant 2026.1 of nieuwer (kamerreiniging vraagt 2026.3 of nieuwer).
- HACS.
- Een Imou-robotstofzuiger die online is op 2,4 GHz wifi en aan je Imou-account gekoppeld is.
- Een gratis ontwikkelaarsaccount op het Imou Open Platform.

Getest is de integratie met de Imou RV-L11-A (L11-serie, met afzuigstation). Twee andere modellen zijn deels geverifieerd door gebruikers:

- **RV1 Pro Max** (deels geverifieerd door een gebruiker): batterij, terug naar het basisstation, zuigkracht (stil, standaard, sterk, max), waterhoeveelheid, starttijd van schema's en kamerreiniging van twee kamers. De robot meldt geen status van het basisstation.
- **RV1L Pro** (deels geverifieerd door een gebruiker): terug naar het basisstation, waterhoeveelheid en extra kracht op tapijt. Geen status van het basisstation.

Andere Imou-robots werken mogelijk ook, maar die zijn niet geverifieerd.

## Stap voor stap instellen

1. **Maak een ontwikkelaarsaccount aan.** Registreer gratis op het Imou Open Platform via [open.imoulife.com](https://open.imoulife.com) en log in.
2. **Voeg je robot toe.** Ga in het Control Board naar Device Access Service en voeg de robot toe met het serienummer en de veiligheidscode (safety code) van het label.
   *Staat er geen veiligheidscode op het label?* Voeg de robot dan toe in de Imou Life-app met de QR-code. Log daarna met dezelfde inloggegevens in op het Open Platform via [auth.imoulife.com/OAuth2/login](https://auth.imoulife.com/OAuth2/login). De robot staat dan onder Device Access Service. Een gebruiker vond deze route. Let op: die gebruiker ontving de verificatiemail niet op een outlook.com-adres.
3. **Haal je App ID en App Secret op.** Open App Information. Houd het App Secret privé.
4. **Installeer via HACS.** Voeg de repository [github.com/okke-mengerink/ha-imou-robot](https://github.com/okke-mengerink/ha-imou-robot) toe als aangepaste repository (categorie Integration) en installeer de integratie. HACS installeert het release-bestand `imou_vacuum.zip`. Lukt het downloaden niet, controleer dan of de release dat zip-bestand bevat. Herstart daarna Home Assistant. De integratie wacht nog op opname in de standaardlijst van HACS. Handmatig installeren kan ook: kopieer de map `custom_components/imou_vacuum` naar je eigen configuratie.
5. **Voeg de integratie toe.** Ga naar Instellingen > Apparaten en diensten > Integratie toevoegen en kies Imou Vacuum (Open Platform). Vul App ID en App Secret in en kies je regio: Europa (standaard), Singapore of Noord-Amerika.

Je robot staat nu in Home Assistant.

### Let op het API-limiet

De integratie werkt via de cloud, en Imou geeft gratis 30.000 API-aanroepen per maand per App ID. Normaal gebruik van de integratie komt uit op ongeveer 13.000 per maand. Gebruik daarom niet dezelfde App ID in de ingebouwde `imou`-integratie van Home Assistant, want die verbruikt je limiet.

## Kamerreiniging (Home Assistant 2026.3+)

Met Home Assistant 2026.3 of nieuwer kun je per ruimte laten schoonmaken. De Imou-API geeft de kaart en de kamerlijst niet door, dus de integratie leest de kamers uit de schema's van de robot in de Imou Life-app. Zo stel je dat in:

1. Maak in de Imou Life-app een schema waarin je de kamers één voor één selecteert. Het schema mag uitgeschakeld zijn.
2. Ga naar Instellingen > Apparaten en diensten > Imou Vacuum (Open Platform) > Configureren en kies **Link rooms to areas** (de menutekst is Engels). Kies per kamer een Home Assistant-ruimte (area). De kamer krijgt de naam van de ruimte en met Home Assistant 2026.3 of nieuwer wordt de kamer ook aan de ruimte gekoppeld. De Imou-app toont geen kamernummers; de knoppen **Clean <kamer>** die de integratie per kamer maakt, helpen om te zien welk nummer welke kamer is. (**Rename rooms (advanced)** in hetzelfde menu is de tekstlijst, per regel één `id = naam`.)
3. Gebruik de actie `vacuum.clean_area`, of vraag Assist om een ruimte schoon te maken.

## Beperkingen

- Geen kaart in Home Assistant.
- Geen zonereiniging.
- Schema's zijn alleen-lezen: je ziet ze, maar past ze aan in de Imou-app.
- Alleen cloud, geen lokale bediening. Zonder internetverbinding of bij een storing bij Imou werkt de bediening niet.

## Veelgestelde vragen

**Is er een Imou-integratie voor robotstofzuigers in Home Assistant?**
Niet in de ingebouwde integratie, en ook niet in Imou Life of Imou-OpenPlatform. Wel in Imou Vacuum (Open Platform), die je via HACS installeert.

**Werkt de integratie met de Imou RV-L11-A?**
Ja, dit model is getest. RV1 Pro Max (deels geverifieerd door een gebruiker): batterij, terug naar het basisstation, zuigkracht (stil, standaard, sterk, max), waterhoeveelheid, starttijd van schema's en kamerreiniging van twee kamers. De robot meldt geen status van het basisstation. RV1L Pro (deels geverifieerd door een gebruiker): terug naar het basisstation, waterhoeveelheid en extra kracht op tapijt. Geen status van het basisstation.

**Kost het iets?**
De integratie is gratis en open source (MIT). Het ontwikkelaarsaccount en de gratis laag van Imou (30.000 API-aanroepen per maand per App ID) zijn ook gratis.

**Werkt het zonder internet?**
Nee. De integratie gebruikt de cloud-API van Imou en heeft geen lokale bediening.

**Mijn model staat er niet bij. Wat nu?**
Andere Imou-robots werken mogelijk. Deel je diagnostiek via een issue op GitHub, dan kijk ik of ik je model kan toevoegen.

**Kan ik de robot ook via Google Home of Alexa bedienen?**
Een stofzuiger-entiteit uit Home Assistant kun je in principe delen via de Google Assistant- of Alexa-integratie van Home Assistant. Met deze integratie is dat niet geverifieerd.

## Conclusie

Als je een Imou robotstofzuiger in Home Assistant wilt gebruiken, is Imou Vacuum (Open Platform) de integratie die dat mogelijk maakt. Je installeert hem via HACS, stelt hem in met een gratis Imou-ontwikkelaarsaccount en bedient je robot daarna als elke andere stofzuiger in Home Assistant.

Je vindt de code, de installatie-instructies en de issue-pagina op GitHub: [github.com/okke-mengerink/ha-imou-robot](https://github.com/okke-mengerink/ha-imou-robot). Loop je ergens tegenaan, of werkt je model nog niet? Open een issue, dan kijk ik ermee mee.

*Okke Mengerink, maker van de integratie.*

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Is er een Imou-integratie voor robotstofzuigers in Home Assistant?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Niet in de ingebouwde integratie, en ook niet in Imou Life of Imou-OpenPlatform. Wel in Imou Vacuum (Open Platform), die je via HACS installeert."
      }
    },
    {
      "@type": "Question",
      "name": "Werkt de integratie met de Imou RV-L11-A?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Ja, dit model is getest. RV1 Pro Max (deels geverifieerd door een gebruiker): batterij, terug naar het basisstation, zuigkracht (stil, standaard, sterk, max), waterhoeveelheid, starttijd van schema's en kamerreiniging van twee kamers. De robot meldt geen status van het basisstation. RV1L Pro (deels geverifieerd door een gebruiker): terug naar het basisstation, waterhoeveelheid en extra kracht op tapijt. Geen status van het basisstation."
      }
    },
    {
      "@type": "Question",
      "name": "Kost het iets?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "De integratie is gratis en open source (MIT). Het ontwikkelaarsaccount en de gratis laag van Imou (30.000 API-aanroepen per maand per App ID) zijn ook gratis."
      }
    },
    {
      "@type": "Question",
      "name": "Werkt het zonder internet?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Nee. De integratie gebruikt de cloud-API van Imou en heeft geen lokale bediening."
      }
    },
    {
      "@type": "Question",
      "name": "Mijn model staat er niet bij. Wat nu?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Andere Imou-robots werken mogelijk. Deel je diagnostiek via een issue op GitHub, dan kijk ik of ik je model kan toevoegen."
      }
    },
    {
      "@type": "Question",
      "name": "Kan ik de robot ook via Google Home of Alexa bedienen?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Een stofzuiger-entiteit uit Home Assistant kun je in principe delen via de Google Assistant- of Alexa-integratie van Home Assistant. Met deze integratie is dat niet geverifieerd."
      }
    }
  ]
}
</script>
