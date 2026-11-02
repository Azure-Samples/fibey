# Fibey Field Ops

A demo agent that helps fiber-optic field technicians look up parts, manage work orders, follow procedures, and check network status.

## Language

**Status Dashboard**:
A web page showing network health (segments, OLT health, alerts, incidents) that is owned by another team or party and has no API. The agent can only read it through a browser.
_Avoid_: NOC page, status API, status service

**Browser Automation**:
The Foundry capability that lets the agent operate a remote browser to read a web page that has no API.
_Avoid_: Web scraping, WebIQ
