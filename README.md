# SiHAS Canary

# 개요

[시하스 Wi-Fi 장치](https://sihas.co.kr/) to [HA](https://www.home-assistant.io/) 연동을 위한 컴포넌트입니다.

<br>



# 지원장치

[Wi-Fi 장치 연동 지원 현황](https://sihas.notion.site/db34d0e1209c4957899b0cc95ba26a4c)

<br>



# 설정

[HomeAssistant 설치 방법](https://sihas.notion.site/HomeAssistant-1385c75ce8b94e1aa812b70bb6e727c3?pvs=4)의 *HACS를 사용하여 설치* 항목을 참조해주세요.

> **NOTE**
>
> 문제가 있을 경우 같은 링크의 *직접 다운로드하여 설치*를 사용해주세요.  
> 해당 방법은 버전 컴포넌트 버전 [`v1.3.3`](https://github.com/cmsong-shina/sihas-canary/releases/tag/v1.3.3)까지 지원합니다.

<br>


## BCM-300 확장 (gm2945 포크)

전원·온도 제어, 온수 3단계(NR-10E 옵션), 재실/외출, 예약, 연소·오류·연결·물 상태를 제공합니다.
HVAC 메뉴는 전원 꺼짐/난방이며, v1.7.12부터 실내·온돌·온수 프리셋과 NR-10E 온수 단계가 기본 표시됩니다. 명시적으로 저장한 옵션은 유지하며 다른 조절기는 구성에서 NR-10E 옵션을 끌 수 있습니다. BCM-300W + 경동 NR-10E에서 사용자가 동작을 확인하여 v1.7.11 안정판으로 배포합니다. 다른 조절기/펌웨어의 호환성은 미확인입니다. 난방세기 쓰기는 아직 지원하지 않습니다.
설치 및 검증 범위는 [BCM-300 안내](custom_components/sihas/docs/BCM300.md)를 확인하세요.

v1.7.13부터 기존 climate에 더해 **전원 switch**와 **운전모드 select(실내/온돌/온수)**를 제공합니다. 두 엔티티는 climate와 같은 실제 상태를 공유합니다. 운전모드 select는 프리셋 전환 옵션을 켠 경우 표시됩니다(기본 ON).
