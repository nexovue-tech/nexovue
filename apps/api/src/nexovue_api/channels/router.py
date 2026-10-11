"""Notification channels (F21, FR23). Reads for any member; every change is an admin's."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayManageChannels
from nexovue_api.channels import service
from nexovue_api.channels.schemas import (
    ChannelListResponse,
    ChannelSummary,
    CreateChannelRequest,
    CreatedChannel,
    DeliveryLogResponse,
    MuteChannelRequest,
    QuietHoursRequest,
    SigningSecret,
    UpdateChannelRequest,
)
from nexovue_api.deps import AppSettings, DbSession

router = APIRouter(prefix="/channels", tags=["alerts"])


@router.get("", summary="Every channel this organization's alerts can go to")
async def list_channels(identity: CurrentIdentity, db: DbSession) -> ChannelListResponse:
    return await service.list_channels(db, org_id=identity.org_id)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Connect a channel")
async def create_channel(
    identity: MayManageChannels, db: DbSession, settings: AppSettings, request: CreateChannelRequest
) -> CreatedChannel:
    return await service.create(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        request=request,
        secret_keys=settings.check_secret_keys,
        email_configured=settings.email is not None,
    )


@router.get("/{channel_id}", summary="One channel, and the rules that send to it")
async def get_channel(
    identity: CurrentIdentity, db: DbSession, channel_id: uuid.UUID
) -> ChannelSummary:
    return await service.get(db, org_id=identity.org_id, channel_id=channel_id)


@router.patch("/{channel_id}", summary="Rename, reconfigure, or replace a channel's secret")
async def update_channel(
    identity: MayManageChannels,
    db: DbSession,
    settings: AppSettings,
    channel_id: uuid.UUID,
    request: UpdateChannelRequest,
) -> ChannelSummary:
    return await service.update(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        channel_id=channel_id,
        request=request,
        secret_keys=settings.check_secret_keys,
    )


@router.post("/{channel_id}/signing-secret", summary="Replace a webhook's signing secret")
async def rotate_signing_secret(
    identity: MayManageChannels, db: DbSession, settings: AppSettings, channel_id: uuid.UUID
) -> SigningSecret:
    return await service.rotate_signing_secret(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        channel_id=channel_id,
        secret_keys=settings.check_secret_keys,
    )


@router.put("/{channel_id}/mute", summary="Mute a channel until a time, or unmute it")
async def mute_channel(
    identity: MayManageChannels, db: DbSession, channel_id: uuid.UUID, request: MuteChannelRequest
) -> ChannelSummary:
    return await service.mute(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        channel_id=channel_id,
        until=request.until,
    )


@router.put("/{channel_id}/quiet-hours", summary="Set or clear a channel's quiet hours")
async def set_quiet_hours(
    identity: MayManageChannels, db: DbSession, channel_id: uuid.UUID, request: QuietHoursRequest
) -> ChannelSummary:
    return await service.set_quiet_hours(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        channel_id=channel_id,
        quiet_hours=request.quiet_hours,
    )


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a channel")
async def delete_channel(identity: MayManageChannels, db: DbSession, channel_id: uuid.UUID) -> None:
    await service.delete(
        db, org_id=identity.org_id, actor_user_id=identity.user.id, channel_id=channel_id
    )


@router.get("/{channel_id}/deliveries", summary="The channel's delivery log, 30 days")
async def delivery_log(
    identity: CurrentIdentity, db: DbSession, channel_id: uuid.UUID
) -> DeliveryLogResponse:
    return await service.delivery_log(db, org_id=identity.org_id, channel_id=channel_id)
