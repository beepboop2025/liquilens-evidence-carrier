{% test liquilens_evidence_contract(model) %}

with invalid_evidence as (
    select *
    from {{ model }}
    where
        carrier_id is null
        or length(carrier_id) <> 33
        or carrier_id not like 'evidence_%'
        or record_hash is null
        or length(record_hash) <> 64
        or event_time is null
        or knowledge_time is null
        or event_time > knowledge_time
        or rights_status not in (
            'licensed', 'allowed', 'metadata_only',
            'restricted', 'unknown', 'blocked'
        )
        or export_disposition not in ('full', 'metadata_only', 'reject')
        or (
            export_disposition <> 'full'
            and coalesce(payload_json, '') <> ''
        )
        or (
            rights_status in ('restricted', 'unknown', 'blocked')
            and export_disposition <> 'reject'
        )
        or (
            export_disposition = 'full'
            and (
                rights_status not in ('licensed', 'allowed')
                or redistribution_permitted <> 'true'
                or (
                    coalesce(rights_license, '') = ''
                    and coalesce(rights_license_url, '') = ''
                )
                or coalesce(rights_attribution, '') = ''
            )
        )
)

select * from invalid_evidence

{% endtest %}
