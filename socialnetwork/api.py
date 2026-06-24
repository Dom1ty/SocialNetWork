from django.db.models import Q, Exists, OuterRef, When, IntegerField, FloatField, Count, ExpressionWrapper, Case, Value, F, Prefetch
from django.template.defaultfilters import first

from fame.models import Fame, FameLevels, FameUsers, ExpertiseAreas
from socialnetwork.models import Posts, SocialNetworkUsers


# general methods independent of html and REST views
# should be used by REST and html views


def _get_social_network_user(user) -> SocialNetworkUsers:
    """Given a FameUser, gets the social network user from the request. Assumes that the user is authenticated."""
    try:
        user = SocialNetworkUsers.objects.get(id=user.id)
    except SocialNetworkUsers.DoesNotExist:
        raise PermissionError("User does not exist")
    return user


def timeline(user: SocialNetworkUsers, start: int = 0, end: int = None, published=True, community_mode=False):
    """Get the timeline of the user. Assumes that the user is authenticated."""


        # T4
        # in community mode, posts of communities are displayed if ALL of the following criteria are met:
        # 1. the author of the post is a member of the community
        # 2. the user is a member of the community
        # 3. the post contains the community’s expertise area
        # 4. the post is published or the user is the author
        #########################
    if community_mode:
        user_communities = user.communities.all()   # 当前 user 是 community member 条件2

        posts = Posts.objects.filter(
            author__communities__in=user_communities, # 1
            expertise_area_and_truth_ratings__in=user_communities, #3
            author__communities=F("expertise_area_and_truth_ratings"), # same comm
        ).filter(
                Q(published=published) | Q(author=user) # 4
            ).distinct().order_by("-submitted")

        #########################

    else:
        # in standard mode, posts of followed users are displayed
        _follows = user.follows.all()
        posts = Posts.objects.filter(
            (Q(author__in=_follows) & Q(published=published)) | Q(author=user)
        ).order_by("-submitted")
    if end is None:
        return posts[start:]
    else:
        return posts[start:end+1]


def search(keyword: str, start: int = 0, end: int = None, published=True):
    """Search for all posts in the system containing the keyword. Assumes that all posts are public"""
    posts = Posts.objects.filter(
        Q(content__icontains=keyword)
        | Q(author__email__icontains=keyword)
        | Q(author__first_name__icontains=keyword)
        | Q(author__last_name__icontains=keyword),
        published=published,
    ).order_by("-submitted")
    if end is None:
        return posts[start:]
    else:
        return posts[start:end+1]


def follows(user: SocialNetworkUsers, start: int = 0, end: int = None):
    """Get the users followed by this user. Assumes that the user is authenticated."""
    _follows = user.follows.all()
    if end is None:
        return _follows[start:]
    else:
        return _follows[start:end+1]


def followers(user: SocialNetworkUsers, start: int = 0, end: int = None):
    """Get the followers of this user. Assumes that the user is authenticated."""
    _followers = user.followed_by.all()
    if end is None:
        return _followers[start:]
    else:
        return _followers[start:end+1]


def follow(user: SocialNetworkUsers, user_to_follow: SocialNetworkUsers):
    """Follow a user. Assumes that the user is authenticated. If user already follows the user, signal that."""
    if user_to_follow in user.follows.all():
        return {"followed": False}
    user.follows.add(user_to_follow)
    user.save()
    return {"followed": True}


def unfollow(user: SocialNetworkUsers, user_to_unfollow: SocialNetworkUsers):
    """Unfollow a user. Assumes that the user is authenticated. If user does not follow the user anyway, signal that."""
    if user_to_unfollow not in user.follows.all():
        return {"unfollowed": False}
    user.follows.remove(user_to_unfollow)
    user.save()
    return {"unfollowed": True}


def submit_post(
    user: SocialNetworkUsers,
    content: str,
    cites: Posts = None,
    replies_to: Posts = None,
):
    """Submit a post for publication. Assumes that the user is authenticated.
    returns a tuple of three elements:
    1. a dictionary with the keys "published" and "id" (the id of the post)
    2. a list of dictionaries containing the expertise areas and their truth ratings
    3. a boolean indicating whether the user was banned and logged out and should be redirected to the login page
    """

    # create post  instance:
    post = Posts.objects.create(
        content=content,
        author=user,
        cites=cites,
        replies_to=replies_to,
    )

    # classify the content into expertise areas:
    # only publish the post if none of the expertise areas contains bullshit:

    _at_least_one_expertise_area_contains_bullshit, _expertise_areas = (
        post.determine_expertise_areas_and_truth_ratings()                     #  _expertise_area is  relevant area
    )
    post.published = not _at_least_one_expertise_area_contains_bullshit        # if there is bullshit ,do not publish

    redirect_to_logout = False


    #########################
    # add your code here        task1 if  relevant fame low , can not publish
    for item in _expertise_areas:
        area = item["expertise_area"]   #返回的是一个dict 先取出 area
        fame_entry = Fame.objects.filter(user=user, expertise_area=area).first()

        rating = item["truth_rating"]
        #task1
        if fame_entry is not None:
            if fame_entry.fame_level.numeric_value < 0: # 当前领域的fame < 0 不能发布
                post.published =  False

        #task2 check 这条post 的truth rating， if  negative 降级
        if rating  is not None and rating.numeric_value < 0:
            if fame_entry is None:
                #turn TO confuser
                Confuser = FameLevels.objects.get(name = "Confuser")
                Fame.objects.create(user=user,
                                    expertise_area=area,
                                    fame_level=Confuser
                                    )
            else:
                #lower rank
                try:
                    fame_entry.fame_level =  fame_entry.fame_level.get_next_lower_fame_level()
                    fame_entry.save()

                    ### task4
                    super_pro = FameLevels.objects.get(name="Super Pro")
                    if fame_entry.fame_level.numeric_value < super_pro.numeric_value :
                        user.communities.remove(area)
                    ### task4
                except ValueError: # 最低 ban user  把他的所有帖子变成unpublished
                    user.is_active = False
                    user.save()
                    Posts.objects.filter(author=user).update(published=False)
                    post.published = False
                    redirect_to_logout = True

    #########################

    post.save()

    return (
        {"published": post.published, "id": post.id},
        _expertise_areas,
        redirect_to_logout,
    )


def rate_post(
    user: SocialNetworkUsers, post: Posts, rating_type: str, rating_score: int
):
    """Rate a post. Assumes that the user is authenticated. If user already rated the post with the given rating_type,
    update that rating score."""
    user_rating = None
    try:
        user_rating = user.userratings_set.get(post=post, rating_type=rating_type)
    except user.userratings_set.model.DoesNotExist:
        pass

    if user == post.author:
        raise PermissionError(
            "User is the author of the post. You cannot rate your own post."
        )

    if user_rating is not None:
        # update the existing rating:
        user_rating.rating_score = rating_score
        user_rating.save()
        return {"rated": True, "type": "update"}
    else:
        # create a new rating:
        user.userratings_set.add(
            post,
            through_defaults={"rating_type": rating_type, "rating_score": rating_score},
        )
        user.save()
        return {"rated": True, "type": "new"}


def fame(user: SocialNetworkUsers):
    """Get the fame of a user. Assumes that the user is authenticated."""
    try:
        user = SocialNetworkUsers.objects.get(id=user.id)
    except SocialNetworkUsers.DoesNotExist:
        raise ValueError("User does not exist")

    return user, Fame.objects.filter(user=user)


def bullshitters():
    """Return a Python dictionary mapping each existing expertise area in the fame profiles to a list of the users
    having negative fame for that expertise area. Each list should contain Python dictionaries as entries with keys
    ``user'' (for the user) and ``fame_level_numeric'' (for the corresponding fame value), and should be ranked, i.e.,
    users with the lowest fame are shown first, in case there is a tie, within that tie sort by date_joined
    (most recent first). Note that expertise areas with no expert may be omitted.
    """
    #########################    area1 -> [ {user: user1, famel_level_numeric: -100) } ,    {user2,-200},      ]  area 2->  [ {user: user1, famel_level_numeric: -200) } ,    {user2,-200},      ]
    result =  {}
    neg_fame = Fame.objects.filter(
        fame_level__numeric_value__lt = 0
    ).order_by("fame_level__numeric_value",
               "-user__date_joined")
    for fame in neg_fame:
        area = fame.expertise_area

        if  area not in result:
            result[area] = []
        result[area].append(
            {
                "user": fame.user,
                "fame_level_numeric" : fame.fame_level.numeric_value,
            }
        )
    return result

    #########################





def join_community(user: SocialNetworkUsers, community: ExpertiseAreas):
    """Join a specified community. Note that this method does not check whether the user is eligible for joining the
    community.
    """

    #########################
    user.communities.add(community)
    #########################



def leave_community(user: SocialNetworkUsers, community: ExpertiseAreas):
    """Leave a specified community."""

    #########################
    user.communities.remove(community)
    #########################



def similar_users(user: SocialNetworkUsers):
    """Compute the similarity of user with all other users. The method returns a QuerySet of FameUsers annotated
    with an additional field 'similarity'. Sort the result in descending order according to 'similarity', in case
    there is a tie, within that tie sort by date_joined (most recent first)"""
                            ##                                  遍历其他用户       遍历我的 fame 领域 如果 对方也有这个领域 且 score 不超过 100 ，加1

    my_fames = Fame.objects.filter(user=user)
    if not my_fames.exists():
        return FameUsers.objects.none()
    scores = {} # diction to store the simlarity
    #遍历其他用户除了自己
    for other_user in FameUsers.objects.exclude(id=user.id):

        similar_count = 0
        # 遍历我的fame
        for my_fame in my_fames:
            other_fame = Fame.objects.filter(
                user=other_user,
                expertise_area = my_fame.expertise_area,
                ).first()
            if other_fame is None:    # 没有结果就不计算fame
                continue
            if abs(my_fame.fame_level.numeric_value - other_fame.fame_level.numeric_value) <= 100:
                similar_count += 1

        similarity = similar_count / my_fames.count()
        if similarity > 0:
            scores[other_user.id] = similarity

    #转化成querySet储存    py dict ---> QuerySet
    whens = []
    for user_id, score in scores.items():
        whens.append(When(id=user_id, then=Value(score)))
    return FameUsers.objects.filter(
        id__in=scores.keys()
    ).annotate(    # annotate是添加临时字段的意思
        similarity=Case(
            *whens,
            output_field=FloatField(),
        )
    ).order_by("-similarity", "-date_joined") # 从小到大 从新到旧