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
HVAC 메뉴는 전원 꺼짐/난방이며, 실내·온돌·온수 프리셋 전환은 구성에서 켤 수 있는 시험 기능입니다. 프리셋 쓰기는 상태 비트에 근거한 추론으로 실기기 검증이 필요합니다. 난방세기 쓰기는 아직 지원하지 않습니다.
설치 및 검증 범위는 [BCM-300 안내](custom_components/sihas/docs/BCM300.md)를 확인하세요.
