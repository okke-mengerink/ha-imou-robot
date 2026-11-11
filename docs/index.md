---
layout: default
title: "Imou robot vacuum in Home Assistant (HACS setup)"
description: "No Imou integration supports robot vacuums. Imou Vacuum (Open Platform) adds your Imou robot to Home Assistant. Free, open source, installs via HACS."
lang: en
permalink: /
image: /images/device-page.png
hreflang:
  - lang: en
    url: /
  - lang: nl
    url: /nl/
  - lang: x-default
    url: /
---

# Imou robot vacuum in Home Assistant

*[Nederlandse versie]({{ "/nl/" | relative_url }})*

Can you control an Imou robot vacuum from Home Assistant? Yes, with the free, open-source integration "Imou Vacuum (Open Platform)", which you install through HACS. The existing Imou integrations do not support robot vacuums, which is probably why your searches came up empty. I built this integration to fill that gap, and this guide walks you through the setup step by step.

![Home Assistant device page of an Imou RV-L11-A with the Imou Vacuum (Open Platform) integration]({{ "/images/device-page.png" | relative_url }})

## Why the existing Imou integrations don't work for vacuums

There are three Imou integrations: Home Assistant's built-in Imou integration, and the HACS integrations Imou Life (`imou_life`) and Imou-OpenPlatform. All three support cameras and other devices, but none supports robot vacuums. You cannot control your robot with any of them, however you configure them.

The integration in this article is made for robot vacuums. It talks to the official Imou Open Platform cloud API.

## What the integration can do

Once it is set up, you get these in Home Assistant:

- A standard vacuum entity: start, pause, resume, stop, return to dock and locate. Suction level is exposed as fan speed.
- Room cleaning (see below).
- Sensors for battery, status, error, cleaned area, cleaning time, consumable usage (brushes, filter, sensors) and Wi-Fi signal.
- Switches, such as carpet boost, and selects for suction and water level.
- A button to empty the bin.
- A Schedules sensor that shows the schedules from the Imou app.
- `vacuum.send_command` for advanced use.

Setup happens in the Home Assistant UI. Entity names are in English.

## What you need

- Home Assistant 2026.1 or newer (room cleaning needs 2026.3 or newer).
- HACS.
- An Imou robot vacuum that is online on 2.4 GHz Wi-Fi and bound to your Imou account.
- A free developer account on the Imou Open Platform.

I tested the integration with the Imou RV-L11-A (L11 series, with auto-empty dock). The Imou RV1 Pro Max has been partly supported since version 0.3.1, based on diagnostics from a user: battery, return to dock and five suction levels. That is not fully verified. Other Imou robots may work too.

## Step-by-step setup

1. **Create a developer account.** Register for free on the Imou Open Platform at [open.imoulife.com](https://open.imoulife.com) and sign in.
2. **Add your robot.** In the Control Board, open Device Access Service and add the robot with the serial number and safety code from the label.
   *No safety code on the label?* Add the robot in the Imou Life app with its QR code first. Then sign in to the Open Platform with the same login at [auth.imoulife.com/OAuth2/login](https://auth.imoulife.com/OAuth2/login). The robot then appears under Device Access Service. A user found this route. Note that this user did not receive the verification mail on an outlook.com address.
3. **Copy your App ID and App Secret.** Open App Information. Keep the App Secret private.
4. **Install through HACS.** Add the repository [github.com/okke-mengerink/ha-imou-robot](https://github.com/okke-mengerink/ha-imou-robot) as a custom repository (category Integration) and install the integration. Restart Home Assistant. The integration is waiting for inclusion in the HACS default store. You can also install it manually by copying `custom_components/imou_vacuum` into your configuration.
5. **Add the integration.** Go to Settings > Devices & services > Add integration and choose Imou Vacuum (Open Platform). Enter the App ID and App Secret and select your region: Europe (default), Singapore or North America.

Your robot is now in Home Assistant.

### Watch the API quota

The integration works through the cloud. Imou's free tier includes 30,000 API calls per month per App ID, and typical use of this integration is about 13,000 per month. Do not run Home Assistant's core `imou` integration with the same App ID, because it would use up the quota.

## Room cleaning (Home Assistant 2026.3+)

With Home Assistant 2026.3 or newer you can clean individual rooms. The Imou API does not report the map or the room list, so the integration reads the rooms from the robot's schedules in the Imou Life app. To set it up:

1. In the Imou Life app, create a schedule that selects the rooms one by one. The schedule may be switched off.
2. Go to Settings > Devices & services > Imou Vacuum (Open Platform) > Configure and choose **Link rooms to areas**. Pick a Home Assistant area for each room. The room takes the area's name, and on Home Assistant 2026.3 or newer it is also linked to the area. The Imou app does not show room numbers; the **Clean <room>** buttons that the integration creates for every room help you find out which number is which room. (**Rename rooms (advanced)** in the same menu is the text list, one `id = name` per line.)
3. Use the action `vacuum.clean_area`, or ask Assist to clean an area.

## Limitations

- No map in Home Assistant.
- No zone cleaning.
- Schedules are read-only: you can see them, but you change them in the Imou app.
- Cloud only, no local control. If your internet connection or Imou's service is down, control stops working.

## FAQ

**Is there an Imou integration for robot vacuums in Home Assistant?**
Not in the built-in integration, and not in Imou Life or Imou-OpenPlatform. Imou Vacuum (Open Platform), installed through HACS, adds it.

**Does it work with the Imou RV-L11-A?**
Yes, this model is tested. The RV1 Pro Max has been partly supported since version 0.3.1.

**Does it cost anything?**
The integration is free and open source (MIT). The developer account and Imou's free tier (30,000 API calls per month per App ID) are free as well.

**Does it work without internet?**
No. The integration uses Imou's cloud API and has no local control.

**My model is not listed. What now?**
Other Imou robots may work. Share your diagnostics in a GitHub issue and I will look at adding your model.

**Can I use it with Google Home or Alexa?**
Like any Home Assistant vacuum entity, it can be exposed through Home Assistant's Google Assistant or Alexa integration. I have not verified this with this integration.

## Conclusion

If you want to use an Imou robot vacuum in Home Assistant, Imou Vacuum (Open Platform) is the integration that makes it possible. Install it through HACS, set it up with a free Imou developer account, and control the robot like any other vacuum in Home Assistant.

You can find the code, install instructions and issue tracker on GitHub: [github.com/okke-mengerink/ha-imou-robot](https://github.com/okke-mengerink/ha-imou-robot). If you run into a problem, or your model does not work yet, open an issue and I will take a look.

*Okke Mengerink, maintainer of the integration.*

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Is there an Imou integration for robot vacuums in Home Assistant?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Not in the built-in integration, and not in Imou Life or Imou-OpenPlatform. Imou Vacuum (Open Platform), installed through HACS, adds it."
      }
    },
    {
      "@type": "Question",
      "name": "Does it work with the Imou RV-L11-A?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes, this model is tested. The RV1 Pro Max has been partly supported since version 0.3.1."
      }
    },
    {
      "@type": "Question",
      "name": "Does it cost anything?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "The integration is free and open source (MIT). The developer account and Imou's free tier (30,000 API calls per month per App ID) are free as well."
      }
    },
    {
      "@type": "Question",
      "name": "Does it work without internet?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "No. The integration uses Imou's cloud API and has no local control."
      }
    },
    {
      "@type": "Question",
      "name": "My model is not listed. What now?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Other Imou robots may work. Share your diagnostics in a GitHub issue and I will look at adding your model."
      }
    },
    {
      "@type": "Question",
      "name": "Can I use it with Google Home or Alexa?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Like any Home Assistant vacuum entity, it can be exposed through Home Assistant's Google Assistant or Alexa integration. I have not verified this with this integration."
      }
    }
  ]
}
</script>
